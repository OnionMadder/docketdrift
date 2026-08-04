"""Load every reporter citation an opinion is known by, from a CL-derived CSV.

``load_reporter_cites`` stores ONE cite per opinion -- whichever CourtListener
listed first. For Arizona that was almost always the Pacific cite, which is why
the official ``123 Ariz. 456`` form (the way AZ opinions most often cite each
other -- present in 87% of a 700-opinion sample) resolved at 0%: we held 151
official cites against 25,335 Pacific ones.

This loads ALL of them into ParallelCite so a citation in any form resolves to
the same opinion. Input CSV columns: ``cluster_id,state,cite,reporter,type``,
matched on ``Opinion.courtlistener_id`` (= CL cluster_id).

Idempotent: existing (opinion, cite) rows are skipped, so re-running is safe
and a partial run can simply be resumed.

Usage::

    python manage.py load_parallel_cites --file parallel_cites.csv
    python manage.py load_parallel_cites --file ... --state AZ --dry-run
"""
from __future__ import annotations

import csv
import time
import collections

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from opinions.models import Opinion, ParallelCite

BATCH = 2000
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP = 3

# Database identifiers, not reporter citations. An opinion is never cited as
# "2005 Minn. LEXIS 123" in the body text of another opinion in this corpus,
# so loading them adds rows that can never resolve. Skipped by default;
# --include-databases keeps them if that ever changes.
_DATABASE_REPORTERS = {"LEXIS", "WL", "U.S. App. LEXIS", "Adv. Rep."}


def _is_database_cite(reporter: str) -> bool:
    r = (reporter or "").strip()
    return ("LEXIS" in r) or r == "WL" or "Adv. Rep." in r or "A.L.R." in r


class Command(BaseCommand):
    help = "Populate ParallelCite from a cluster_id,state,cite,reporter,type CSV."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--state", default=None,
                            help="Restrict to one state (USPS code).")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--include-databases", action="store_true",
                            help="Also load LEXIS/WL/A.L.R. identifiers "
                                 "(skipped by default -- they never appear as "
                                 "citations in our opinion text).")

    def handle(self, *args, file, state, dry_run, include_databases, **opts):
        # Batch job: the 25s web cap from settings would kill the id map build.
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        want_state = (state or "").upper() or None

        self.stdout.write("building cluster_id -> opinion_id map...")
        cl_map = {}
        for oid, clid in Opinion.objects.exclude(
                courtlistener_id=None).values_list("id", "courtlistener_id"):
            if clid:
                cl_map[str(clid)] = oid
        self.stdout.write("  %d opinions carry a CL cluster id" % len(cl_map))

        # Existing pairs, so re-runs don't fight the unique constraint.
        have = set(ParallelCite.objects.values_list("opinion_id", "cite"))
        self.stdout.write("  %d parallel cites already present" % len(have))

        pending = []
        stats = collections.Counter()
        seen_pairs = set()

        def flush():
            if not pending or dry_run:
                del pending[:]
                return
            for attempt in range(1, DB_MAX_RETRIES + 1):
                try:
                    ParallelCite.objects.bulk_create(pending, ignore_conflicts=True)
                    break
                except BaseException as exc:
                    if attempt >= DB_MAX_RETRIES:
                        raise
                    self.stderr.write("  batch failed (%s); reconnect %d/%d"
                                      % (type(exc).__name__, attempt, DB_MAX_RETRIES))
                    try:
                        connection.close()
                    except BaseException:
                        pass
                    time.sleep(DB_RETRY_SLEEP)
            del pending[:]

        try:
            fh = open(file, encoding="utf8")
        except OSError as exc:
            raise CommandError("cannot open %s: %s" % (file, exc))

        with fh:
            reader = csv.DictReader(fh)
            for row in reader:
                stats["rows"] += 1
                st = (row.get("state") or "").upper()
                if want_state and st != want_state:
                    continue
                reporter = (row.get("reporter") or "").strip()
                if not include_databases and _is_database_cite(reporter):
                    stats["skipped_database"] += 1
                    continue
                oid = cl_map.get((row.get("cluster_id") or "").strip())
                if not oid:
                    stats["no_matching_opinion"] += 1
                    continue
                cite = (row.get("cite") or "").strip()[:64]
                if not cite:
                    continue
                key = (oid, cite)
                if key in have or key in seen_pairs:
                    stats["already_present"] += 1
                    continue
                seen_pairs.add(key)
                stats["loaded"] += 1
                stats["state_" + st] += 1
                pending.append(ParallelCite(
                    opinion_id=oid, cite=cite, reporter=reporter[:32]))
                if len(pending) >= BATCH:
                    flush()
            flush()

        self.stdout.write(self.style.SUCCESS(
            "%s rows=%d loaded=%d already=%d no-opinion=%d db-ids-skipped=%d"
            % ("DRY RUN --" if dry_run else "done.", stats["rows"],
               stats["loaded"], stats["already_present"],
               stats["no_matching_opinion"], stats["skipped_database"])))
        for k in sorted(k for k in stats if k.startswith("state_")):
            self.stdout.write("   %s: %d" % (k[6:], stats[k]))
