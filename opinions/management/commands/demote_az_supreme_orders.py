"""Mark Arizona Supreme Court review ORDERS non-precedential and drop their cite.

WHY
---
A Supreme Court order on a petition for review ("ORDERED: Petition for Review
= DENIED ... the Court of Appeals' Opinion shall not be published, pursuant to
Rule 111(g)") is a ruling on an application, not an opinion. CourtListener
files it under the case's cluster as "Published" and it inherits the
court-of-appeals opinion's reporter cite, so the stub page claims to BE
``81 P.3d 1016`` (159 stored rows, 1990-2015; 136 share their cite with a real
opinion). 2026-10-26.

WHAT
----
For AZ Supreme rows that are precedential, under ``--max-chars`` (1,500), and
open as an order (``parsing.az.is_supreme_review_order``): set
``is_precedential = False`` and clear ``reporter_cite``.

Caveat: ``load_reporter_cites`` fills EMPTY cites from CL's export, so a
re-run of it would restore the borrowed cite; skip these ids if it is re-run.

Dry-run by default; ``--apply`` to commit. Idempotent (only precedential rows
are candidates).
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Court, Opinion
from opinions.parsing.az import is_supreme_review_order

SCAN = 1000


class Command(BaseCommand):
    help = "Demote AZ Supreme petition-for-review orders stored as precedential."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--max-chars", type=int, default=1500)

    def handle(self, *args, apply, max_chars, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")
        cids = list(Court.objects.filter(state_id="AZ", level="SUPREME")
                    .values_list("id", flat=True))
        self.stdout.write("Demote AZ Supreme orders%s" % ("" if apply else "  (DRY RUN)"))
        t0 = time.time()
        hits, years = [], {}
        examples = []
        for cid in cids:
            last = 0
            while True:
                with connection.cursor() as cur:
                    cur.execute(
                        "SELECT id, case_number, release_date, reporter_cite, "
                        "LEFT(raw_text, 1500) FROM opinions_opinion "
                        "WHERE court_id = %s AND id > %s AND is_precedential = 1 "
                        "AND CHAR_LENGTH(raw_text) < %s ORDER BY id LIMIT %s",
                        [cid, last, max_chars, SCAN])
                    rows = cur.fetchall()
                if not rows:
                    break
                for pk, cn, d, cite, head in rows:
                    last = pk
                    if is_supreme_review_order(head):
                        hits.append(pk)
                        years[d.year // 5 * 5] = years.get(d.year // 5 * 5, 0) + 1
                        if len(examples) < 8:
                            examples.append("%s %s %s" % (pk, cn, cite))
        if apply:
            for i in range(0, len(hits), 200):
                Opinion.objects.filter(pk__in=hits[i:i + 200]).update(
                    is_precedential=False, reporter_cite="")
        self.stdout.write("%s %d orders in %.1fs; by half-decade %s" % (
            "demoted" if apply else "WOULD demote", len(hits),
            time.time() - t0, sorted(years.items())))
        self.stdout.write("examples: " + " | ".join(examples))
