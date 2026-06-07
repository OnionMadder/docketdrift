"""Resolve judges + auto-create PanelVote rows from opinion text.

The CL bulk load brought in ~3,610 PanelVote rows -- only ~6% of the
60K corpus has structured panel data. The remaining ~57K opinions DO
contain panel info in their raw_text (typical MN format: "Filed June 1,
2026 / Affirmed / Larson, Judge" + "Considered and decided by Larson,
Judge; Bjorkman, Judge; and Wheelock, Judge"), they just never got
matched to Judge model rows.

This command does that match: parses each opinion, extracts the byline
author + the panel list, looks up each name against ``state``'s Judge
table by last-name, and creates ``PanelVote`` rows with the appropriate
vote_type. Idempotent via ``get_or_create`` on the existing
``(opinion, judge)`` unique constraint -- re-runs only ever ADD votes,
never modify existing ones (except a Pass-1 upgrade from MAJORITY_JOIN
to MAJORITY_AUTHOR when the same judge turns out to be the byline
author).

Match strategy: last-name only, case-insensitive. Ambiguous last names
(multiple judges with the same surname) are skipped + counted in the
summary so the editor can disambiguate manually. Acceptable miss rate
for v1 -- the alternative is a per-judge alias table.

Usage::

    python manage.py resolve_judges            # full MN pass
    python manage.py resolve_judges --state MN --limit 500 --dry-run
    python manage.py resolve_judges --since 2020-01-01  # only recent

Cost: regex-only, no API calls. ~10-20 minutes for the full corpus
since each opinion's raw_text gets re-parsed.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import date

from django.core.management.base import BaseCommand

from opinions.models import Judge, Opinion, PanelVote


# Strip the role suffix (", Judge" / ", Justice" / etc.) off a byline.
_ROLE_SUFFIX_RE = re.compile(
    r",\s*(?:Chief\s+)?(?:Judge|Justice|J\.|C\.J\.)\.?\s*$",
    re.IGNORECASE,
)


def _last_name(name: str) -> str:
    """Return the last token of ``name`` after stripping role suffix.

    'Jennifer L. Frisch'      -> 'Frisch'
    'Frisch, Judge'           -> 'Frisch'
    'L. Frisch, J.'           -> 'Frisch'
    'Van Buren, Judge'        -> 'Buren'   (acceptable miss for v1)
    """
    if not name:
        return ""
    cleaned = _ROLE_SUFFIX_RE.sub("", name).strip()
    # Re-strip just in case ",..." remains
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0].strip()
    words = cleaned.split()
    if not words:
        return ""
    return words[-1].strip(".,'-")


class Command(BaseCommand):
    help = "Resolve byline + panel names to Judges and auto-create PanelVote rows."

    def add_arguments(self, parser):
        parser.add_argument("--state", default="MN", help="State code (default MN).")
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Process at most N opinions (smoke-test convenience).",
        )
        parser.add_argument(
            "--since", default=None,
            help="Only opinions filed >= YYYY-MM-DD. Useful for incremental re-runs.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Compute matches + counts but don't create PanelVote rows.",
        )

    def handle(self, *args, state, limit, since, dry_run, **options):
        # Local import: parsing module loads the state-parser registry
        from opinions.parsing import parse as parse_opinion

        state_code = state.upper()

        # Build last_name -> [Judge,...] lookup for the state. Ambiguity
        # (multiple judges sharing a last name) gets logged + skipped.
        judges = list(Judge.objects.filter(state__code=state_code))
        last_name_map: dict[str, list[Judge]] = defaultdict(list)
        for j in judges:
            ln = _last_name(j.full_name)
            if ln:
                last_name_map[ln.lower()].append(j)

        ambiguous_names = sum(1 for v in last_name_map.values() if len(v) > 1)
        unique_names = sum(1 for v in last_name_map.values() if len(v) == 1)

        self.stdout.write(self.style.SUCCESS(
            f"Resolving panels for {state_code}: "
            f"{len(judges)} judges, "
            f"{unique_names} unique-last-name lookups, "
            f"{ambiguous_names} ambiguous (skipped)"
        ))

        opinion_qs = (
            Opinion.objects.filter(court__state__code=state_code)
            .exclude(raw_text="")
            .select_related("court")
        )
        if since:
            try:
                cutoff = date.fromisoformat(since)
            except ValueError:
                self.stderr.write(f"Bad --since date: {since!r}; use YYYY-MM-DD.")
                return
            opinion_qs = opinion_qs.filter(release_date__gte=cutoff)

        total = opinion_qs.count()
        if limit:
            total = min(total, limit)

        self.stdout.write(
            f"  scanning {total:,} opinions"
            + (f" filed since {since}" if since else "")
            + ("." if not dry_run else " (DRY RUN; no DB writes).")
        )

        scanned = 0
        author_resolved = panel_resolved = 0
        author_ambiguous = panel_ambiguous = 0
        new_author_votes = new_join_votes = upgraded_votes = 0
        t0 = time.time()

        for opinion in opinion_qs.iterator(chunk_size=500):
            if limit and scanned >= limit:
                break
            scanned += 1

            if scanned % 2_000 == 0:
                elapsed = time.time() - t0
                rate = scanned / max(elapsed, 0.001)
                eta = (total - scanned) / max(rate, 0.001)
                self.stdout.write(
                    f"  scanned {scanned:>6,}/{total:,}  "
                    f"author={new_author_votes:>5,}  "
                    f"join={new_join_votes:>5,}  "
                    f"upgraded={upgraded_votes:>4,}  "
                    f"({rate:>4.0f}/s, eta {eta/60:.0f}min)",
                    ending="\n",
                )

            result = parse_opinion(state_code, opinion.raw_text)
            if result is None:
                continue

            # ---- Pass 1: Author ----
            author_judge: Judge | None = None
            if result.author:
                author_last = _last_name(result.author).lower()
                matches = last_name_map.get(author_last, [])
                if len(matches) == 1:
                    author_judge = matches[0]
                    author_resolved += 1
                elif len(matches) > 1:
                    author_ambiguous += 1

            if author_judge and not dry_run:
                pv, created = PanelVote.objects.get_or_create(
                    opinion=opinion,
                    judge=author_judge,
                    defaults={"vote_type": PanelVote.Vote.MAJORITY_AUTHOR},
                )
                if created:
                    new_author_votes += 1
                elif pv.vote_type == PanelVote.Vote.MAJORITY_JOIN:
                    # Existing CL-loaded row only knew "joined majority";
                    # parser confirms this judge actually authored.
                    pv.vote_type = PanelVote.Vote.MAJORITY_AUTHOR
                    pv.save(update_fields=["vote_type"])
                    upgraded_votes += 1

            # ---- Pass 2: Panel members ----
            for panel_entry in result.panel:
                panel_last = _last_name(panel_entry).lower()
                matches = last_name_map.get(panel_last, [])
                if not matches:
                    continue
                if len(matches) > 1:
                    panel_ambiguous += 1
                    continue

                panel_judge = matches[0]
                if author_judge is not None and panel_judge.pk == author_judge.pk:
                    # Already counted as author; don't downgrade to "joined".
                    continue
                panel_resolved += 1

                if not dry_run:
                    _, created = PanelVote.objects.get_or_create(
                        opinion=opinion,
                        judge=panel_judge,
                        defaults={"vote_type": PanelVote.Vote.MAJORITY_JOIN},
                    )
                    if created:
                        new_join_votes += 1

        elapsed = time.time() - t0
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done in {elapsed/60:.1f} min."
        ))
        self.stdout.write(
            f"  scanned:           {scanned:>7,}\n"
            f"  authors resolved:  {author_resolved:>7,}  "
            f"(ambiguous skipped: {author_ambiguous})\n"
            f"  panels resolved:   {panel_resolved:>7,}  "
            f"(ambiguous skipped: {panel_ambiguous})\n"
            f"  new author votes:  {new_author_votes:>7,}\n"
            f"  new joined votes:  {new_join_votes:>7,}\n"
            f"  upgraded (J->A):   {upgraded_votes:>7,}"
            + ("\n  (DRY RUN -- nothing saved)" if dry_run else "")
        )
