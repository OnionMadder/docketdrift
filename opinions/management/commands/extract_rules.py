# -*- coding: utf-8 -*-
"""Populate RuleCitation from opinion text.

Driven the same way every long backfill in this repo is driven, because
NFSN culls anything that runs too long and the failure modes are silent:

  * rc=152 SIGXCPU at ~40s of CPU -- the command must SELF-EXIT via
    ``--max-runtime`` or it dies before printing its resume line and the
    next tick replays the same range forever.
  * rc=137 SIGKILL on memory -- ``qs.iterator()`` on Django's MySQL
    backend buffers the WHOLE result client-side (341K x 11KB = 3.7GB).
    pk-windowed short queries instead, never iterator.
  * The resume trailer is printed at EVERY progress mark, not just at
    the end, so a kill resumes near the death point rather than at the
    start of the pass.

The ``resume with:  --min-id N`` trailer carries a DOUBLE space. Wrappers
grep that exact string; a single-space print once cost a day of
min-id=0 loops.
"""
import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Court, Opinion, RuleCitation
from opinions.parsing.rules import extract_rules

PROGRESS_EVERY = 2000


class Command(BaseCommand):
    help = "Extract court-rule citations from opinion text into RuleCitation."

    def add_arguments(self, parser):
        parser.add_argument("--state", required=True,
                            help="USPS code, e.g. MN.")
        parser.add_argument("--min-id", type=int, default=0,
                            help="Resume cursor: scan opinions with pk > this.")
        parser.add_argument("--max-runtime", type=int, default=0,
                            help="Self-exit after N seconds (0 = run to completion).")
        parser.add_argument("--id-batch", type=int, default=400,
                            help="Opinions fetched per window.")
        parser.add_argument("--limit", type=int, default=0,
                            help="Stop after N opinions (0 = no limit).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Extract and report, write nothing.")

    def handle(self, *args, **opts):
        state = opts["state"].upper()
        started = time.time()
        max_runtime = opts["max_runtime"]
        batch_size = opts["id_batch"]
        limit = opts["limit"]
        dry = opts["dry_run"]

        # settings.py caps every connection at 25s, which is right for web
        # requests and fatal for a corpus scan (errno 1969 / 2013).
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        # Pre-resolve court ids: turns a JOIN+COUNT over the 2.75GB table
        # into an FK-index lookup.
        court_ids = list(
            Court.objects.filter(state__code=state).values_list("id", flat=True))
        if not court_ids:
            self.stderr.write("No courts for state %s" % state)
            return

        cursor = opts["min_id"]
        scanned = 0
        with_rules = 0
        created = 0
        boiler = 0
        by_set = {}
        last_mark = 0

        def trailer(cur_id):
            # DOUBLE space before --min-id; wrappers grep this exact string.
            self.stdout.write("resume with:  --min-id %d" % cur_id)

        while True:
            rows = list(
                Opinion.objects.filter(court_id__in=court_ids, pk__gt=cursor)
                .order_by("pk")
                .values_list("pk", "raw_text")[:batch_size]
            )
            if not rows:
                break

            pending = []
            pks = []
            for pk, text in rows:
                cursor = pk
                scanned += 1
                pks.append(pk)
                refs = extract_rules(state, text or "")
                if refs:
                    with_rules += 1
                for r in refs:
                    by_set[r.rule_set] = by_set.get(r.rule_set, 0) + 1
                    if r.is_boilerplate:
                        boiler += 1
                    pending.append(RuleCitation(
                        opinion_id=pk,
                        reference_slug=r.reference_slug,
                        reference_display=r.reference_display[:128],
                        rule_set=r.rule_set,
                        rule_number=r.rule_number,
                        subdivision=r.subdivision,
                        subsection=r.subsection[:16],
                        is_boilerplate=r.is_boilerplate,
                        text_offset=r.text_offset,
                    ))

            if not dry:
                # Delete-then-insert for the WINDOW (two queries, not two
                # per opinion) keeps the command idempotent: re-running
                # after an extractor change rebuilds cleanly rather than
                # doubling every row.
                RuleCitation.objects.filter(opinion_id__in=pks).delete()
                if pending:
                    RuleCitation.objects.bulk_create(pending, batch_size=500)
            created += len(pending)

            if scanned - last_mark >= PROGRESS_EVERY:
                last_mark = scanned
                self.stdout.write(
                    "  scanned %d  opinions-with-rules %d  cites %d  (%.0fs)"
                    % (scanned, with_rules, created, time.time() - started))
                trailer(cursor)
                self.stdout.flush()

            if limit and scanned >= limit:
                break
            if max_runtime and (time.time() - started) >= max_runtime:
                self.stdout.write("max-runtime reached")
                break

        self.stdout.write("")
        self.stdout.write("state=%s scanned=%d  opinions with >=1 rule cite=%d"
                          % (state, scanned, with_rules))
        self.stdout.write("rule cites %s: %d  (boilerplate disclaimer: %d)"
                          % ("found" if dry else "written", created, boiler))
        for k in sorted(by_set, key=lambda x: -by_set[x]):
            self.stdout.write("    %-14s %6d" % (k, by_set[k]))
        trailer(cursor)
