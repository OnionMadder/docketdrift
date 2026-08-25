"""Vote-level purge of LA party-name leak votes on otherwise-real judges.

``audit_la_learned_judges`` culled judges NEVER extracted by the fixed
pipeline. This command handles the subtler residue it deliberately
kept: MIXED rows, where a real judge's row also carries votes minted by
the old panel-blob overrun from opinions in which the "vote" is really
the case PARTY sharing the surname (measured 2026-08-25: Judge Mitchell
Theriot's row held a 2007 vote from an opinion whose Theriot is the
DEFENDANT, Voohries J. Theriot; same for Miller/Greene/Jenkins/Atkins).

Per-vote evidence gate, conservative by construction:

- Re-extract the vote's opinion with the FIXED pipeline (state parser +
  generic byline extractor, union filtered through ``_valid_surname``
  exactly as ``resolve_judges`` accepts names).
- Surname present in the union -> KEEP (real participation).
- Surname absent AND the parser extracted a NON-EMPTY panel -> DELETE:
  we affirmatively know who sat, and this judge is not among them.
- Surname absent but no panel could be extracted -> KEEP and count:
  an unextractable opinion is uncertainty, and deleting a real vote is
  worse than keeping an uncertain one.

Scope: every PanelVote belonging to a judge on an LA court. The walk is
judge-by-judge (indexed vote fetch, PK point-reads for opinion text —
no JOIN against the 2.75GB table, per the plan-flip gotchas), with
per-opinion extraction memoized across judges. Dry-run by default;
``--apply`` to commit.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Judge, Opinion, PanelVote


class Command(BaseCommand):
    help = "Delete LA panel votes refuted by the fixed extraction (vote-level)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually delete (default: dry-run report).")

    def handle(self, *args, apply, **options):
        from opinions.parsing import parse as parse_opinion
        from opinions.management.commands.resolve_judges import (
            _extract_generic_byline,
            _last_name,
            _valid_surname,
        )
        from opinions.models import Court

        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        court_ids = list(
            Court.objects.filter(state__code="LA").values_list("id", flat=True)
        )
        judges = list(
            Judge.objects.filter(court_id__in=court_ids).order_by("pk")
        )

        # opinion_id -> (frozenset of accepted surnames, panel_known)
        memo: dict[int, tuple[frozenset, bool]] = {}

        def extract(opinion_id: int):
            hit = memo.get(opinion_id)
            if hit is not None:
                return hit
            try:
                text = (Opinion.objects.only("raw_text")
                        .get(pk=opinion_id).raw_text)
            except Opinion.DoesNotExist:
                text = ""
            names: set[str] = set()
            panel_known = False
            if text:
                result = parse_opinion("LA", text)
                if result is not None:
                    if result.panel:
                        panel_known = True
                    if result.author:
                        names.add(_last_name(result.author).lower())
                    for p in result.panel:
                        names.add(_last_name(p).lower())
                generic = _extract_generic_byline(text)
                if generic.author_last:
                    names.add(generic.author_last)
                names.update(generic.panel_last)
                names.update(generic.dissenter_last)
                names.update(generic.concurrer_last)
                names = {n for n in names if _valid_surname(n)}
            out = (frozenset(names), panel_known)
            memo[opinion_id] = out
            return out

        t0 = time.time()
        kept = deleted = uncertain = 0
        per_judge: list[tuple[int, str, int]] = []

        for judge in judges:
            surname = _last_name(judge.full_name).lower()
            doomed: list[int] = []
            for vote_id, opinion_id in PanelVote.objects.filter(
                    judge=judge).values_list("id", "opinion_id"):
                names, panel_known = extract(opinion_id)
                if surname in names:
                    kept += 1
                elif panel_known:
                    doomed.append(vote_id)
                else:
                    uncertain += 1
            if doomed:
                deleted += len(doomed)
                per_judge.append((judge.pk, judge.full_name, len(doomed)))
                if apply:
                    PanelVote.objects.filter(id__in=doomed).delete()

        mode = "APPLIED" if apply else "DRY RUN"
        elapsed = time.time() - t0
        self.stdout.write(self.style.SUCCESS(
            f"\n{mode} in {elapsed/60:.1f} min: judges={len(judges):,} "
            f"kept={kept:,} deleted={deleted:,} uncertain-kept={uncertain:,} "
            f"(opinions parsed: {len(memo):,})"
        ))
        for pk, name, n in sorted(per_judge, key=lambda x: -x[2])[:30]:
            self.stdout.write(f"    {n:>5,}  pk={pk} {name}")
