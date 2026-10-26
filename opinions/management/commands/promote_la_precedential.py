"""Set is_precedential=True on LA circuit APPEALS wrongly stored as unpublished.

WHY
---
CourtListener reports ``precedential_status = "Unknown"`` for every Louisiana
circuit opinion from 2019 on (checked 2026-10-25 on First/Third/Fifth), and
the loaders stored "not Published" as False. Under La. Rule 2-16.3 an opinion
is published unless it carries the "NOT DESIGNATED FOR PUBLICATION" notice, so
for APPEAL dockets the notice (or its absence) is the court's own designation.

SAFETY
------
* Onion approved the "appeals only" scope 2026-10-25. This is the one command
  that PROMOTES False -> True; every other repair only demotes.
* Candidate = LA APPEALS court, release_date >= --since (default 2019-01-01),
  currently False, appeal-type docket and >= 2,500 chars
  (``la_designated_appeal``), and no notice in the head
  (``text_says_nonprecedential``). Writ dockets (KW/CW/KH/C/K...) never move.
* Dry-run by default; ``--apply`` to commit. Cull-safe: ``--max-runtime``.
  Re-running is idempotent (only False rows are candidates), so a cull needs
  no cursor: just run it again.

Usage::

    python manage.py promote_la_precedential
    python manage.py promote_la_precedential --apply --max-runtime 300
"""
from __future__ import annotations

import collections
import re
import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Court, Opinion
from opinions.parsing import NONPRECEDENTIAL_HEAD_CHARS, text_says_nonprecedential
from opinions.parsing.la import la_designated_appeal

SCAN = 1000


class Command(BaseCommand):
    help = "Promote LA circuit appeals stored as unpublished with no notice."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--since", default="2019-01-01")
        parser.add_argument("--max-runtime", type=int, default=0)

    def handle(self, *args, apply, since, max_runtime, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")
        courts = {c.id: c.short_label for c in Court.objects.filter(
            state_id="LA", level="APPEALS")}
        self.stdout.write("Promote LA appeals%s" % ("" if apply else "  (DRY RUN)"))
        t0 = time.time()
        scanned = promoted = 0
        by_court = collections.Counter()
        kinds = collections.Counter()
        examples = []
        stopped = False
        for cid in courts:
            last = 0
            while True:
                if max_runtime and time.time() - t0 >= max_runtime:
                    stopped = True
                    break
                with connection.cursor() as cur:
                    cur.execute(
                        "SELECT id, case_number, CHAR_LENGTH(raw_text), "
                        "LEFT(raw_text, %s) FROM opinions_opinion "
                        "WHERE court_id = %s AND id > %s AND is_precedential = 0 "
                        "AND release_date >= %s ORDER BY id LIMIT %s",
                        [NONPRECEDENTIAL_HEAD_CHARS, cid, last, since, SCAN])
                    rows = cur.fetchall()
                if not rows:
                    break
                hits = []
                for pk, cn, ln, head in rows:
                    scanned += 1
                    last = pk
                    if (la_designated_appeal(cn, ln)
                            and not text_says_nonprecedential("LA", head or "")):
                        hits.append(pk)
                        kinds["/".join(re.findall(r"[A-Za-z]+", cn or ""))] += 1
                        if len(examples) < 10:
                            examples.append("%s %s" % (pk, cn))
                promoted += len(hits)
                by_court[courts[cid]] += len(hits)
                if hits and apply:
                    Opinion.objects.filter(pk__in=hits).update(is_precedential=True)
            if stopped:
                self.stdout.write("stopped early on --max-runtime; re-run to continue")
                break
        self.stdout.write("\nscanned %d, %s %d in %.1f min" % (
            scanned, "promoted" if apply else "WOULD promote", promoted,
            (time.time() - t0) / 60.0))
        for k, n in by_court.most_common():
            self.stdout.write("  %-28s %6d" % (k, n))
        self.stdout.write("kinds: " + ", ".join("%s=%d" % kv for kv in kinds.most_common(12)))
        self.stdout.write("examples: " + " | ".join(examples))
