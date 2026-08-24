"""Remove PanelVote rows created by parenthetical CITATIONS, not panels.

Two verified leaks, both found by the corroboration guard in
``backfill_nh_judge_status`` (CL said the justice had left years before
the vote's opinion was filed):

  David Hackett Souter -- MAJORITY_AUTHOR on State v. Burris (2017-0409,
  filed 2018-06-05). Souter left the New Hampshire Supreme Court in 1990.
  The opinion's text contains:
      "State v. Bradberry, 129 N.H. 68, 82-83 (1986)
       (Souter, J., concurring specially)"
  -- a citation to his own 1986 concurrence, read as a byline.

  William F. Batchelder -- MAJORITY_JOIN on 2002-468 (filed 2003-08-01),
  whose text contains:
      "Cf Cormier, 127 N.H. at 260 (Batchelder, J., concurring specially)"

Both are the failure mode CLAUDE.md documents: a "SURNAME, J.,
concurring" INSIDE parentheses is a citation to another opinion, while a
bare one is this court's signoff. ``resolve_judges._inside_open_paren``
guards that at extraction time now; these rows predate it and were never
swept up.

DELIBERATELY NOT DELETED: Batchelder's 1997 and 1999 votes. His surname
does not appear in those opinions' text at all, so they came from
CourtListener's bulk panel data rather than from our byline extraction.
New Hampshire does recall retired justices to sit, so these may be real
service after his 1995 termination -- and deleting a real vote is worse
than keeping an uncertain one. They are left alone and noted here.

Idempotent (a re-run finds nothing). Dry-run by default; --apply commits.
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Judge, Opinion, PanelVote

# (judge surname, opinion case_number) -- each verified by reading the
# opinion text and confirming the surname appears ONLY inside a
# parenthetical citation.
LEAKS = [
    ("Souter", "2017-0409"),
    ("Batchelder", "2002-468"),
]


class Command(BaseCommand):
    help = "Delete PanelVote rows that came from parenthetical citations (NH)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Commit. Omit for a dry-run preview.")

    def handle(self, *args, apply, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        mode = "APPLY" if apply else "DRY-RUN (nothing deleted; pass --apply)"
        self.stdout.write(self.style.SUCCESS(f"cleanup_nh_citation_leaks — {mode}\n"))
        deleted = 0

        for surname, case_number in LEAKS:
            judge = Judge.objects.filter(
                state__code="NH", full_name__icontains=surname).first()
            if not judge:
                self.stdout.write(self.style.WARNING(f"  no NH judge matching {surname!r}"))
                continue
            op = Opinion.objects.filter(
                court__state__code="NH", case_number=case_number
            ).only("id", "case_number", "release_date").first()
            if not op:
                self.stdout.write(self.style.WARNING(f"  no NH opinion {case_number!r}"))
                continue

            pv = PanelVote.objects.filter(judge=judge, opinion_id=op.id).first()
            if not pv:
                self.stdout.write(f"  {surname} / {case_number}: already clean")
                continue

            # Re-verify at run time rather than trusting the list: the
            # surname must appear ONLY inside parentheses in this opinion.
            # If a bare (real) signoff exists too, this is not a pure leak
            # and we must not delete it.
            raw = Opinion.objects.only("raw_text").get(pk=op.id).raw_text or ""
            bare = 0
            for m in re.finditer(re.escape(surname), raw):
                pre = raw[max(0, m.start() - 150):m.start()]
                if pre.rfind("(") <= pre.rfind(")"):   # not inside an open paren
                    bare += 1
            if bare:
                self.stdout.write(self.style.WARNING(
                    f"  {surname} / {case_number}: {bare} BARE mention(s) found -- "
                    "not a pure citation leak, SKIPPED"))
                continue

            self.stdout.write(
                f"  {surname} / {case_number} ({op.release_date}): "
                f"deleting {pv.vote_type} -- citation-only mention")
            if apply:
                pv.delete()
            deleted += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Deleted' if apply else 'Would delete'}: {deleted} vote(s)."))
        if not apply:
            self.stdout.write("Re-run with --apply to commit.")
