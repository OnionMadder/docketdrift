"""Merge duplicate Judge rows for one person who served on TWO courts.

A judge elevated from an intermediate appellate court to a supreme court
gets TWO CourtListener person records -- one per seat -- and our loader
faithfully created a Judge row from each. The result is one human with
two dossiers, two partial vote histories, and a surname that now looks
ambiguous to ``resolve_judges`` (which then discards their bylines
rather than guess between "them" and "themselves").

Each pair below was verified individually against four signals before
being listed here:
  - different CourtListener person ids (so this is not a re-run artifact)
  - DIFFERENT courts within the same state, in the expected order
    (intermediate appellate first, supreme second)
  - appointment dates consistent with an elevation, not two careers
  - vote spans that do not overlap in a way implying two people

DELIBERATELY EXCLUDED: "Barry A. Anderson" / "Russell A. Anderson"
(MN). The audit's duplicate heuristic flagged them because they share a
surname and a middle initial, but they are two different Minnesota
justices. Merging them would erase a real person. Kept here as a
comment so nobody "completes" the list later.

Also excluded: LA's "Ii" / "Iii" rows -- Roman-numeral fragments
captured as names. Those are junk to DELETE, not duplicates to merge,
and deletion is a separate decision.

Idempotent: a pair whose loser row is already gone is reported and
skipped. Dry-run by default; --apply commits.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from opinions.judge_merge import merge_judge
from opinions.models import Judge

# (state, loser_full_name, survivor_full_name, why)
PAIRS = [
    ("AZ", "Arthur John Pelander", "John Pelander",
     "COA 1995 -> Supreme 2009; cl 6236/3720"),
    ("AZ", "Rebecca White Berch", "Rebecca W. Berch",
     "COA 1998 -> Supreme 2002; cl 6179/3697"),
    ("AZ", "James Cameron", "James D. Cameron",
     "COA 1965 -> Supreme 1971; cl 6184/3700"),
    ("AZ", "Eg Noyes jr", "E G. Noyes jr",
     "same court + same year, consecutive cl ids 6231/6230 -- OCR variant"),
    ("MN", "G Barry Anderson", "Barry A. Anderson",
     "COA 1998 (33 votes) -> Supreme 2004; cl 7609/4806"),
]


class Command(BaseCommand):
    help = "Merge Judge rows that are one person holding two court seats."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Commit. Omit for a dry-run preview.")

    def handle(self, *args, apply, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        mode = "APPLY" if apply else "DRY-RUN (nothing written; pass --apply)"
        self.stdout.write(self.style.SUCCESS(f"merge_cross_court_judges — {mode}\n"))

        merged = skipped = 0
        total_moved = total_dedup = 0

        for code, loser_name, survivor_name, why in PAIRS:
            loser = Judge.objects.filter(state__code=code, full_name=loser_name).first()
            survivor = Judge.objects.filter(state__code=code, full_name=survivor_name).first()

            if loser is None:
                self.stdout.write(f"  [{code}] {loser_name!r}: already merged/absent")
                skipped += 1
                continue
            if survivor is None:
                self.stdout.write(self.style.WARNING(
                    f"  [{code}] survivor {survivor_name!r} not found -- SKIPPED"))
                skipped += 1
                continue
            if loser.pk == survivor.pk:
                self.stdout.write(f"  [{code}] {loser_name!r}: same row, nothing to do")
                skipped += 1
                continue

            # Guard: refuse if BOTH rows carry a CL id and they are equal --
            # that would mean the same source record, i.e. a loader bug
            # rather than an elevation, and the right fix is upstream.
            if (loser.courtlistener_id and survivor.courtlistener_id
                    and loser.courtlistener_id == survivor.courtlistener_id):
                self.stdout.write(self.style.WARNING(
                    f"  [{code}] {loser_name!r}: identical CL id on both rows -- SKIPPED"))
                skipped += 1
                continue

            moved, dedup = merge_judge(loser, survivor, apply=apply)
            self.stdout.write(
                f"  [{code}] {loser_name!r} -> {survivor_name!r}: "
                f"{moved} vote(s) moved, {dedup} deduped   ({why})")
            merged += 1
            total_moved += moved
            total_dedup += dedup

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Merged' if apply else 'Would merge'}: {merged} pair(s), "
            f"{total_moved} vote(s) moved, {total_dedup} deduped, {skipped} skipped."))
        if not apply:
            self.stdout.write("Re-run with --apply to commit.")
