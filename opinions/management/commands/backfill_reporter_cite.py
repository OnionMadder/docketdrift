"""Backfill Opinion.reporter_cite from each state parser's citation extraction.

Idempotent -- only fills rows where reporter_cite is currently empty. Run after
migration 0024. Re-runnable as new opinions land (though the parser also sets it
on ingest going forward).

Only NH opinions currently carry an extractable cite: the neutral
"<year> N.H. <n>" printed in the slip opinion. MN/AZ reporter cites
(N.W.2d, P.3d) are assigned by the reporter after publication and are NOT
in our opinion text -- they await a CourtListener citation backfill, so
running this for MN/AZ is a harmless no-op for now.

Usage::

    python manage.py backfill_reporter_cite --state NH
    python manage.py backfill_reporter_cite            # every parsered state
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from opinions.models import Opinion
from opinions.parsing import parse as parse_state


class Command(BaseCommand):
    help = "Populate Opinion.reporter_cite from the state parsers (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state", default=None,
            help="USPS 2-letter code (e.g. NH). Default: every state with a parser.",
        )
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Stop after scanning N opinions (smoke test).",
        )

    def handle(self, *args, state, limit, **options):
        qs = (
            Opinion.objects.filter(reporter_cite="")
            .exclude(raw_text="")
            .select_related("court__state")
        )
        if state:
            qs = qs.filter(court__state__code=state.upper())

        scanned = filled = 0
        for op in qs.iterator(chunk_size=200):
            scanned += 1
            result = parse_state(op.court.state.code, op.raw_text)
            if result and result.reporter_cite:
                Opinion.objects.filter(pk=op.pk).update(
                    reporter_cite=result.reporter_cite
                )
                filled += 1
            if limit and scanned >= limit:
                break
            if scanned % 2000 == 0:
                self.stdout.write("  scanned=%d filled=%d" % (scanned, filled))

        self.stdout.write(self.style.SUCCESS(
            "Done. scanned=%d filled=%d" % (scanned, filled)
        ))
