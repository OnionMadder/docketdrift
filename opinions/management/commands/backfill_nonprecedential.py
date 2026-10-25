"""Flip ``is_precedential`` to False where the opinion's own header says so.

WHY THIS EXISTS
---------------
The MN parser recognized only the post-2020 "This opinion is nonprecedential"
wording. Older unpublished opinions open "This opinion will be unpublished and
may not be cited except as provided by Minn. Stat. § 480A.08, subd. 3", so
every one ingested from PDF defaulted to precedential=True: 4,072 opinions from
the 2017-2020 archive backfill, plus 44 older rows (measured 2026-10-24, found
via a removal request on A16-2084). The parser is fixed; this repairs stored
rows.

SAFETY
------
* Dry-run by default; ``--apply`` to commit.
* ONE direction only: True -> False. Absence of the marker is weak evidence
  (the field defaults to True), so this never promotes anything.
* Text evidence only: the state parser over the HEAD of the opinion
  (``text_says_nonprecedential``, first 3,000 chars), so a body quoting the
  notice can't flip it and each parser's own guards apply (AZ: COA only).
* Cull-safe: ``--max-runtime`` + the shared ``resume with:  --min-id N``
  trailer (DOUBLE space).

Usage::

    python manage.py backfill_nonprecedential --state MN
    python manage.py backfill_nonprecedential --state MN --apply --max-runtime 300
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from opinions.models import Court, Opinion
from opinions.parsing import (
    NONPRECEDENTIAL_HEAD_CHARS as HEAD_CHARS, REGISTRY, text_says_nonprecedential)

SCAN = 1000


class Command(BaseCommand):
    help = "Set is_precedential=False where the opinion's header says unpublished."

    def add_arguments(self, parser):
        parser.add_argument("--state", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--min-id", type=int, default=0)
        parser.add_argument("--max-runtime", type=int, default=0)

    def handle(self, *args, state, apply, min_id, max_runtime, **options):
        code = state.upper()
        if code not in REGISTRY:
            raise CommandError("no parser registered for state %s" % code)

        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        court_ids = list(Court.objects.filter(state__code=code)
                         .values_list("id", flat=True))
        if not court_ids:
            raise CommandError("no courts for state %s" % code)

        self.stdout.write("Nonprecedential repair for %s%s"
                          % (code, "" if apply else "  (DRY RUN)"))
        scanned = flipped = 0
        examples = []
        last_pk = int(min_id or 0)
        t0 = time.time()
        stopped_early = False

        while True:
            if max_runtime and time.time() - t0 >= max_runtime:
                stopped_early = True
                break
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT id, case_number, LEFT(raw_text, %s) FROM opinions_opinion "
                    "WHERE id > %s AND is_precedential = 1 AND court_id IN ("
                    + ",".join(["%s"] * len(court_ids)) + ") ORDER BY id LIMIT %s",
                    [HEAD_CHARS, last_pk, *court_ids, SCAN])
                rows = cur.fetchall()
            if not rows:
                break
            hits = []
            for pk, docket, head in rows:
                scanned += 1
                last_pk = pk
                if text_says_nonprecedential(code, head or ""):
                    hits.append(pk)
                    if len(examples) < 10:
                        examples.append(docket)
            flipped += len(hits)
            if hits and apply:
                Opinion.objects.filter(pk__in=hits).update(is_precedential=False)
            self.stdout.write("  scanned %6d  flipped %5d" % (scanned, flipped))
            self.stdout.write("resume with:  --min-id %d" % last_pk)

        self.stdout.write(
            "\nDone in %.1f min%s. scanned %d, %s %d. examples: %s"
            % ((time.time() - t0) / 60.0,
               " (stopped early on --max-runtime)" if stopped_early else "",
               scanned, "flipped" if apply else "WOULD flip", flipped,
               ", ".join(examples)))
        self.stdout.write("resume with:  --min-id %d" % last_pk)
