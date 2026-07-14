"""Load Opinion.reporter_cite from a CL-derived ``cluster_id,reporter_cite`` CSV.

MN/AZ reporter cites (N.W.2d, P.3d) aren't in our opinion text -- they're
reporter-assigned after publication. They come from CourtListener's
``search_citation`` bulk export, extracted offline (best cite per cluster) and
matched here by ``courtlistener_id`` (= CL cluster_id).

Idempotent: fills only rows where ``reporter_cite`` is currently empty, so
NH's parser-derived neutral cites are never overwritten and re-runs are safe.
Batched with retry-and-reconnect (NFSN's shared MariaDB drops big/slow reads),
and lifts the 25s web cap since this is a batch job.

Usage::

    python manage.py load_reporter_cites --file mnaz_reporter_cites.csv
    python manage.py load_reporter_cites --file ... --state AZ --dry-run
"""
from __future__ import annotations

import csv
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from opinions.models import Court, Opinion

BATCH = 500
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP = 3


class Command(BaseCommand):
    help = "Populate Opinion.reporter_cite from a cluster_id,reporter_cite CSV (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to the cluster_id,reporter_cite CSV.")
        parser.add_argument("--state", default=None, help="Restrict updates to one state (USPS code).")
        parser.add_argument("--dry-run", action="store_true", help="Count what would fill; write nothing.")

    def handle(self, *args, file, state, dry_run, **opts):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        mapping = {}
        try:
            with open(file, newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                next(reader, None)  # header
                for row in reader:
                    if len(row) >= 2 and row[0] and row[1]:
                        mapping[row[0]] = row[1]
        except OSError as exc:
            raise CommandError(f"Cannot read {file!r}: {exc}")
        self.stdout.write(f"Mapping: {len(mapping):,} cluster->cite entries.")
        if not mapping:
            return

        court_ids = None
        if state:
            court_ids = list(
                Court.objects.filter(state__code=state.upper()).values_list("id", flat=True)
            )
            if not court_ids:
                raise CommandError(f"No courts found for state {state!r}.")

        cluster_ids = list(mapping.keys())
        total = len(cluster_ids)
        scanned = filled = 0
        for start in range(0, total, BATCH):
            chunk = cluster_ids[start:start + BATCH]
            for attempt in range(1, DB_MAX_RETRIES + 1):
                try:
                    qs = Opinion.objects.filter(courtlistener_id__in=chunk, reporter_cite="")
                    if court_ids is not None:
                        qs = qs.filter(court_id__in=court_ids)
                    objs = list(qs.only("id", "courtlistener_id", "reporter_cite"))
                    for o in objs:
                        o.reporter_cite = mapping.get(o.courtlistener_id, "")
                    objs = [o for o in objs if o.reporter_cite]
                    if objs and not dry_run:
                        Opinion.objects.bulk_update(objs, ["reporter_cite"])
                    scanned += len(chunk)
                    filled += len(objs)
                    break
                except BaseException as exc:
                    # 2013 lost-connection / 1969 / SSL EINTR (KeyboardInterrupt).
                    # Bare BaseException per the CLAUDE.md gotcha.
                    if attempt >= DB_MAX_RETRIES:
                        raise
                    self.stderr.write(
                        f"  batch @{start} failed ({type(exc).__name__}: {exc}); "
                        f"reconnecting {attempt}/{DB_MAX_RETRIES}..."
                    )
                    try:
                        connection.close()
                    except BaseException:
                        pass
                    time.sleep(DB_RETRY_SLEEP)
            if start and start % (BATCH * 20) == 0:
                self.stdout.write(f"  scanned={scanned:,} filled={filled:,}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. clusters={total:,}  scanned={scanned:,}  filled={filled:,}"
            + ("  (DRY RUN -- nothing written)" if dry_run else "")
        ))
