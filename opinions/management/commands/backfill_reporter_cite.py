"""Backfill Opinion.reporter_cite from each state parser's citation extraction.

Idempotent -- only fills rows where reporter_cite is currently empty. Run after
migration 0024.

Only NH opinions currently carry an extractable cite: the neutral
"<year> N.H. <n>" printed in the slip opinion. MN/AZ reporter cites
(N.W.2d, P.3d) are assigned by the reporter after publication and are NOT
in our opinion text -- they await a CourtListener citation backfill, so
running this for MN/AZ is a harmless no-op for now.

Processes in independent ID-keyed batches (NOT a streaming ``.iterator()``):
a single server-side cursor held open across the whole slow, per-row-parsing
sweep gets dropped by NFSN's MariaDB as a long-held connection (2013). Each
batch is a short PK-IN query, with retry-and-reconnect around it.

Usage::

    python manage.py backfill_reporter_cite --state NH
    python manage.py backfill_reporter_cite            # every parsered state
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Opinion
from opinions.parsing import parse as parse_state

BATCH = 200
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP = 3


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
        qs = Opinion.objects.filter(reporter_cite="")
        if state:
            qs = qs.filter(court__state__code=state.upper())

        # IDs only -- one quick query, no raw_text. Then fetch + parse in
        # independent batches so we never hold a cursor open across the whole
        # sweep (which NFSN's MariaDB drops as a long-held connection -> 2013).
        ids = list(qs.values_list("id", flat=True))
        if limit:
            ids = ids[:limit]
        total = len(ids)
        self.stdout.write("Scanning %d opinion(s) for reporter cites..." % total)

        scanned = filled = 0
        for start in range(0, total, BATCH):
            chunk = ids[start:start + BATCH]
            for attempt in range(1, DB_MAX_RETRIES + 1):
                try:
                    rows = list(
                        Opinion.objects.filter(id__in=chunk).select_related("court__state")
                    )
                    for op in rows:
                        result = parse_state(op.court.state.code, op.raw_text)
                        if result and result.reporter_cite:
                            Opinion.objects.filter(pk=op.pk).update(
                                reporter_cite=result.reporter_cite
                            )
                            filled += 1
                    scanned += len(rows)
                    break
                except BaseException as exc:
                    # Lost connection (2013) or NFSN SSL EINTR (raises
                    # KeyboardInterrupt) -- reconnect and retry this batch.
                    # Bare BaseException per the CLAUDE.md gotcha.
                    if attempt >= DB_MAX_RETRIES:
                        raise
                    self.stderr.write(
                        "  batch @%d failed (%s: %s); reconnecting %d/%d..."
                        % (start, type(exc).__name__, exc, attempt, DB_MAX_RETRIES)
                    )
                    try:
                        connection.close()
                    except BaseException:
                        pass
                    time.sleep(DB_RETRY_SLEEP)
            if start and start % (BATCH * 25) == 0:
                self.stdout.write("  scanned=%d filled=%d" % (scanned, filled))

        self.stdout.write(self.style.SUCCESS(
            "Done. scanned=%d filled=%d" % (scanned, filled)
        ))
