"""Populate/repair the slim embedding table from Opinion.embedding.

Batched per (court, year) so every INSERT..SELECT is small enough for the
shared DB: the source read uses the release_date index, the insert lands in
clustered (court_id, release_date) order, and ON DUPLICATE KEY makes re-runs
idempotent. Bounded (--max-runtime) and resumable (batches that already ran
are no-ops), per the standard NFSN pattern.

Also prunes rows whose fat-table counterpart changed release_date or court
(rare -- e.g. the A16-1688 "Filed June 5, 21017" typo repair) or was deleted:
a slim row keyed on stale (court, date) would otherwise shadow the real one.

Usage::

    python manage.py sync_embedding_table                # all states
    python manage.py sync_embedding_table --state MN --max-runtime 400
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Court, State

DB_MAX_RETRIES = 5
DB_RETRY_SLEEP = 3


class Command(BaseCommand):
    help = "Backfill/repair opinions_opinionembedding from the fat table (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--state", default=None,
                            help="USPS code; default all live states.")
        parser.add_argument("--max-runtime", type=int, default=0,
                            help="Self-exit after N seconds (0 = run to done).")
        parser.add_argument("--prune", action="store_true",
                            help="Also delete slim rows with no matching fat row.")

    def _retry(self, fn):
        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                return fn()
            except BaseException as exc:
                if attempt >= DB_MAX_RETRIES:
                    raise
                self.stderr.write("  retry %d after %s"
                                  % (attempt, type(exc).__name__))
                try:
                    connection.close()
                except BaseException:
                    pass
                time.sleep(DB_RETRY_SLEEP)

    def handle(self, *args, state, max_runtime, prune, **options):
        started = time.time()
        codes = ([state.upper()] if state else
                 list(State.objects.filter(is_live=True)
                      .values_list("code", flat=True)))

        def lift():
            with connection.cursor() as c:
                c.execute("SET SESSION max_statement_time = 0")
        self._retry(lift)

        total = 0
        for code in codes:
            court_ids = list(Court.objects.filter(state__code=code)
                             .values_list("id", flat=True))
            for cid in court_ids:
                def years():
                    with connection.cursor() as c:
                        c.execute("SET SESSION max_statement_time = 0")
                        c.execute(
                            "SELECT MIN(YEAR(release_date)), MAX(YEAR(release_date)) "
                            "FROM opinions_opinion WHERE court_id = %s", [cid])
                        return c.fetchone()
                lo, hi = self._retry(years) or (None, None)
                if lo is None:
                    continue
                for year in range(lo, hi + 1):
                    if max_runtime and (time.time() - started) > max_runtime:
                        self.stdout.write(self.style.WARNING(
                            "time budget hit at %s court=%s year=%d; re-run to "
                            "continue (completed batches are no-ops)"
                            % (code, cid, year)))
                        return

                    def batch():
                        with connection.cursor() as c:
                            c.execute("SET SESSION max_statement_time = 0")
                            c.execute(
                                "INSERT INTO opinions_opinionembedding "
                                "  (court_id, release_date, opinion_id, embedding) "
                                "SELECT court_id, release_date, id, embedding "
                                "FROM opinions_opinion "
                                "WHERE court_id = %s AND embedding_pending = 0 "
                                "  AND release_date >= %s AND release_date < %s "
                                "ON DUPLICATE KEY UPDATE "
                                "  embedding = VALUES(embedding)",
                                [cid, "%d-01-01" % year, "%d-01-01" % (year + 1)])
                            return c.rowcount
                    n = self._retry(batch) or 0
                    total += n
                    if n:
                        self.stdout.write("  %s court=%s %d: %d rows"
                                          % (code, cid, year, n))

        if prune:
            def do_prune():
                with connection.cursor() as c:
                    c.execute("SET SESSION max_statement_time = 0")
                    # Anti-join on the PK triple: any slim row whose fat
                    # counterpart moved (date/court changed) or vanished.
                    c.execute(
                        "DELETE e FROM opinions_opinionembedding e "
                        "LEFT JOIN opinions_opinion o "
                        "  ON o.id = e.opinion_id "
                        " AND o.court_id = e.court_id "
                        " AND o.release_date = e.release_date "
                        "WHERE o.id IS NULL")
                    return c.rowcount
            pruned = self._retry(do_prune) or 0
            self.stdout.write("pruned %d stale rows" % pruned)

        def counts():
            with connection.cursor() as c:
                c.execute("SET SESSION max_statement_time = 0")
                c.execute("SELECT COUNT(*) FROM opinions_opinionembedding")
                return c.fetchone()[0]
        self.stdout.write(self.style.SUCCESS(
            "done. upserted=%d  slim-table rows=%d" % (total, self._retry(counts))))
