"""Report-only audit of LA byline-learned (court-NULL) judge rows.

The pre-2026-08-25 LA panel extraction could mint PARTY surnames as
judges (the "Bolton" class: an uncut panel-composed sentence ran into
"Defendant, Robert Bolton, appeals..."). ``cleanup_la_junk_judges``
removed the verified junk-token rows; this command finds the residue
that LOOKS like a real name: for every court-NULL judge with votes, it
re-extracts a sample of that judge's own vote opinions with the FIXED
pipeline (state parser + generic extractor, filtered through
``_valid_surname`` exactly as ``resolve_judges`` now does) and reports
any judge whose surname is NEVER extracted.

REPORT ONLY -- nothing is deleted. The output is the candidate list for
a follow-up evidence-gated cull (same discipline as
``cleanup_la_junk_judges``: explicit pks, re-verified at apply time).

A judge OK'd on any sampled opinion is skipped silently. Sampling is
capped (``SAMPLE_CAP``) for high-vote judges -- a judge with hundreds
of votes where NO sample extracts is exactly the anomaly worth human
eyes, not proof, which is why this reports rather than deletes.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count

from opinions.models import Judge, PanelVote

SAMPLE_CAP = 8


class Command(BaseCommand):
    help = "Report court-NULL LA judges never extracted by the fixed pipeline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-id", type=int, default=0,
            help="Skip judges with pk <= this (resume support).",
        )
        parser.add_argument(
            "--max-runtime", type=int, default=0,
            help="Self-exit after N seconds at a judge boundary.",
        )

    def handle(self, *args, min_id, max_runtime, **options):
        from opinions.parsing import parse as parse_opinion
        from opinions.management.commands.resolve_judges import (
            _extract_generic_byline,
            _last_name,
            _valid_surname,
        )

        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        judges = (
            Judge.objects.filter(court__isnull=True, pk__gt=min_id)
            .annotate(n=Count("panel_votes"))
            .filter(n__gt=0)
            .order_by("pk")
        )

        t0 = time.time()
        checked = flagged = 0
        last_pk = min_id
        stopped_early = False

        for judge in judges.iterator(chunk_size=200):
            if max_runtime and (time.time() - t0) >= max_runtime:
                stopped_early = True
                break
            last_pk = judge.pk
            checked += 1
            surname = _last_name(judge.full_name).lower()

            sampled = 0
            found = False
            for vote in (PanelVote.objects.filter(judge=judge)
                         .select_related("opinion")[:SAMPLE_CAP]):
                text = vote.opinion.raw_text
                if not text:
                    continue
                sampled += 1
                names = set()
                result = parse_opinion("LA", text)
                if result is not None:
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
                if surname in {n for n in names if _valid_surname(n)}:
                    found = True
                    break

            if not found:
                flagged += 1
                why = ("no sampled opinion had text"
                       if sampled == 0 else
                       f"never extracted across {sampled} sampled opinions")
                self.stdout.write(
                    f"  [FLAG] pk={judge.pk} {judge.full_name!r} "
                    f"({judge.n} votes) -- {why}"
                )

        tag = " (stopped early on --max-runtime)" if stopped_early else ""
        self.stdout.write(self.style.SUCCESS(
            f"\nChecked {checked:,} court-NULL judges;{tag} "
            f"flagged {flagged:,}."
        ))
        if stopped_early:
            self.stdout.write(f"resume with:  --min-id {last_pk}")
