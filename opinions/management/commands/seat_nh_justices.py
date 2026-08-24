"""Apply the OFFICIAL New Hampshire Supreme Court roster to Judge rows.

Every value written here was read from the court's own site on
2026-08-22, not from recall:

  https://www.courts.nh.gov/our-courts/supreme-court/about/justices
    - the five sitting justices, with swearing-in dates and seat numbers
    - a "Recent retirements" list giving each departing justice's ROLE
      AT DEPARTURE and retirement date
  https://www.courts.nh.gov/our-courts/supreme-court/about
    - "named for Frank Rowe Kenison, who served as Chief Justice for
      25 years"

That sourcing is the point. The roster had 30 of 37 judges sitting at
status=UNKNOWN / role=UNKNOWN, including four former Chief Justices, and
the fix for that is a citation -- not a plausible guess about who was
Chief when.

TWO THINGS THIS DELIBERATELY DOES NOT DO:

1. It does not set Status.SENIOR for anyone. New Hampshire titles its
   longest-serving associate "Senior Associate Justice" as a courtesy
   rank; that is NOT the federal-style "senior status" (semi-retired)
   our Status.SENIOR means. Donovan is a full active justice. Writing
   SENIOR would misstate the record in exactly the direction a reader
   would misread.

2. It does not touch the ~24 historical justices for whom this source
   says nothing. Their status stays UNKNOWN, which is honest, rather
   than being back-filled from vote recency. "We have not verified this"
   beats a tidy-looking assertion nobody checked.

Slugs are never rewritten -- they are the public URL key, and an
existing link must not 404 because a name got a middle initial.

Idempotent. Dry-run by default; pass --apply to commit.
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand

from opinions.models import Judge, State


# (match_name, full_name, role, status, seated, appointment_date)
#
# match_name is what the row currently holds; full_name is the court's
# own spelling. Where they differ it is a name COMPLETION (initials,
# suffix orthography), never a different person.
SITTING = [
    ("Gordon J. MacDonald",   "Gordon J. MacDonald",   Judge.Role.CHIEF_JUSTICE,     date(2021, 3, 4)),
    ("Patrick E. Donovan",    "Patrick E. Donovan",    Judge.Role.ASSOCIATE_JUSTICE, date(2018, 5, 8)),
    ("Melissa Beth Countway", "Melissa Beth Countway", Judge.Role.ASSOCIATE_JUSTICE, date(2024, 1, 2)),
    ("Bryan Gould",           "Bryan K. Gould",        Judge.Role.ASSOCIATE_JUSTICE, date(2025, 9, 18)),
    ("Daniel Will",           "Daniel E. Will",        Judge.Role.ASSOCIATE_JUSTICE, date(2026, 3, 4)),
]

# From the court's "Recent retirements" list. The role recorded is the
# role held AT DEPARTURE, which is why Lynn and Dalianis are CHIEF.
RETIRED = [
    ("Anna Hantz Marconi", "Anna Barbara Hantz Marconi", Judge.Role.ASSOCIATE_JUSTICE),
    ("James P. Bassett",   "James P. Bassett",           Judge.Role.ASSOCIATE_JUSTICE),
    ("Gary E. Hicks",      "Gary E. Hicks",              Judge.Role.ASSOCIATE_JUSTICE),
    ("Robert J. Lynn",     "Robert J. Lynn",             Judge.Role.CHIEF_JUSTICE),
    ("Linda S. Dalianis",  "Linda S. Dalianis",          Judge.Role.CHIEF_JUSTICE),
    ("Carol Ann Conboy",   "Carol Ann Conboy",           Judge.Role.ASSOCIATE_JUSTICE),
    # Sourced from the Supreme Court building's naming, on the About page.
    ("Frank Rowe Kenison", "Frank Rowe Kenison",         Judge.Role.CHIEF_JUSTICE),
]

# Orthography only. CourtListener's people DB stores generational
# suffixes lowercase ("jr") and as bare digits ("3"); both render badly
# on a public roster. Fixing spelling asserts nothing about the person.
ORTHOGRAPHY = {
    "John T. Broderick jr":  "John T. Broderick Jr.",
    "Sherman D. Horton jr":  "Sherman D. Horton Jr.",
    "Amos Noyes Blandin jr": "Amos Noyes Blandin Jr.",
    "Walter Stephen Thayer 3": "Walter Stephen Thayer III",
    "Charles G. Douglas 3":  "Charles G. Douglas III",
}


class Command(BaseCommand):
    help = "Apply the official NH Supreme Court roster (sourced 2026-08-22)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Commit changes. Omit for a dry-run preview.",
        )

    def handle(self, *args, apply, **options):
        nh = State.objects.get(code="NH")
        mode = "APPLY" if apply else "DRY-RUN (nothing written; pass --apply)"
        self.stdout.write(self.style.SUCCESS(f"seat_nh_justices — {mode}\n"))

        changed = missing = 0

        def get(name):
            return Judge.objects.filter(state=nh, full_name=name).first()

        def apply_changes(judge, fields: dict, label: str):
            nonlocal changed
            deltas = {
                k: (getattr(judge, k), v)
                for k, v in fields.items()
                if getattr(judge, k) != v
            }
            if not deltas:
                self.stdout.write(f"    {label:<28} already correct")
                return
            for k, (old, new) in deltas.items():
                self.stdout.write(f"    {label:<28} {k}: {old!r} -> {new!r}")
            if apply:
                for k, v in fields.items():
                    setattr(judge, k, v)
                judge.save(update_fields=list(fields))
            changed += 1

        self.stdout.write("  SITTING (5 seats; court says 'Chief Justice and four Associate Justices')")
        for match, full, role, appt in SITTING:
            j = get(match) or get(full)
            if not j:
                self.stdout.write(self.style.WARNING(f"    MISSING: {match!r}"))
                missing += 1
                continue
            apply_changes(j, {
                "full_name": full,
                "role": role,
                "status": Judge.Status.ACTIVE,
                "is_currently_seated": True,
                "appointment_date": appt,
            }, full)

        self.stdout.write("\n  RETIRED (from the court's own 'Recent retirements' list)")
        for match, full, role in RETIRED:
            j = get(match) or get(full)
            if not j:
                self.stdout.write(self.style.WARNING(f"    MISSING: {match!r}"))
                missing += 1
                continue
            apply_changes(j, {
                "full_name": full,
                "role": role,
                "status": Judge.Status.RETIRED,
                "is_currently_seated": False,
            }, full)

        self.stdout.write("\n  ORTHOGRAPHY (suffix spelling only; slugs untouched)")
        for old, new in ORTHOGRAPHY.items():
            j = get(old)
            if not j:
                self.stdout.write(f"    {old!r}: not present (already fixed?)")
                continue
            apply_changes(j, {"full_name": new}, new)

        # Anyone still seated who is NOT on the official list is stale --
        # a justice who left the bench without the flag being cleared.
        official = {full for _, full, _, _ in SITTING}
        strays = (Judge.objects.filter(state=nh, is_currently_seated=True)
                  .exclude(full_name__in=official))
        if strays.exists():
            self.stdout.write(self.style.WARNING(
                "\n  STILL FLAGGED SEATED but not on the official roster:"))
            for j in strays:
                self.stdout.write(f"    {j.full_name} ({j.slug})")
                if apply:
                    j.is_currently_seated = False
                    j.save(update_fields=["is_currently_seated"])
                    changed += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Applied' if apply else 'Would change'}: {changed} judge(s)."
            + (f"  MISSING rows: {missing}" if missing else "")
        ))
        if not apply:
            self.stdout.write("Re-run with --apply to commit.")
