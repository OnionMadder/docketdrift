"""Merge duplicate AZ Judge rows introduced by the first scrape_judges
run.

The first run of ``manage.py scrape_judges az`` filtered the
last-name lookup to court=ariz (Supreme Court), which missed the
CL-bulk-loaded judges whose ``court`` FK is NULL. As a result every
scraped justice was inserted as a NEW Judge, producing pairs of
records like:

    id=185  Ann A. Scott Timmer  source_id=cl-3727                  panel_votes=1, bio=""
    id=328  Ann A. Scott Timmer  source_id=azcourts:chief-justice... panel_votes=0, bio=597 chars

The fix to scrape_judges removes the court filter so re-runs match
correctly. This command cleans up the rows the first run already
created:

  1. Find every Judge in state=AZ whose source_id starts with
     ``azcourts:`` AND has 0 panel_votes (the scraper-created stubs).
  2. For each such "new" row, find a sibling Judge in the same state
     whose last-name matches and whose source_id does NOT start with
     azcourts: (the canonical CL-bulk / byline row that has panel
     vote history).
  3. If exactly one sibling exists, COPY the bio / photo / role /
     court / source_id from the scraper row onto the sibling, then
     DELETE the scraper row.
  4. If zero or multiple siblings exist, leave the scraper row in
     place and log it for human review.

Idempotent: re-running after a successful merge finds no
``azcourts:`` rows with 0 panel votes and does nothing.

Usage::

    python manage.py reconcile_az_judges --dry-run
    python manage.py reconcile_az_judges
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from opinions.models import Judge, PanelVote


class Command(BaseCommand):
    help = "Merge duplicate AZ Judge rows from the first scrape_judges az run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the merge plan; don't write to the DB.",
        )

    def handle(self, *args, dry_run, **options):
        # Build a last-name -> [Judge] index over ALL AZ judges so the
        # sibling lookup is one O(N) sweep.
        all_az = list(Judge.objects.filter(state__code="AZ"))
        last_name_map: dict[str, list[Judge]] = defaultdict(list)
        for j in all_az:
            tokens = (j.full_name or "").strip().split()
            if not tokens:
                continue
            last_name_map[tokens[-1].lower().strip(",.;:")].append(j)

        # Scraper-created stubs to evaluate for merge.
        candidates = [
            j for j in all_az
            if (j.source_id or "").startswith("azcourts:")
        ]
        if not candidates:
            self.stdout.write("No azcourts: rows found. Nothing to do.")
            return

        merged = skipped = 0
        for stub in candidates:
            n_pv_stub = PanelVote.objects.filter(judge=stub).count()
            if n_pv_stub > 0:
                # Scraper row somehow gained panel votes -- leave alone.
                self.stdout.write(
                    f"  SKIP id={stub.id} {stub.full_name!r}: has {n_pv_stub} panel votes already"
                )
                skipped += 1
                continue

            tokens = (stub.full_name or "").strip().split()
            if not tokens:
                skipped += 1
                continue
            last_lower = tokens[-1].lower().strip(",.;:")

            siblings = [
                j for j in last_name_map.get(last_lower, [])
                if j.id != stub.id and not (j.source_id or "").startswith("azcourts:")
            ]

            if len(siblings) == 0:
                # No canonical sibling -- the scraper row is the only
                # source for this last name. Just promote it to be the
                # canonical row (already is, really) and move on.
                self.stdout.write(
                    f"  KEEP id={stub.id} {stub.full_name!r}: no CL-bulk sibling found"
                )
                continue

            if len(siblings) > 1:
                # Ambiguous sibling set -- human review.
                self.stdout.write(self.style.WARNING(
                    f"  AMBIG id={stub.id} {stub.full_name!r}: "
                    f"{len(siblings)} candidate siblings -- review manually"
                ))
                skipped += 1
                continue

            canonical = siblings[0]
            n_pv_can = PanelVote.objects.filter(judge=canonical).count()
            self.stdout.write(
                f"  MERGE stub id={stub.id} -> canonical id={canonical.id} "
                f"{canonical.full_name!r} (panel_votes={n_pv_can})"
            )
            if dry_run:
                merged += 1
                continue

            with transaction.atomic():
                # Copy scraper fields to canonical
                canonical.role = stub.role
                canonical.status = stub.status
                canonical.is_currently_seated = stub.is_currently_seated
                canonical.bio_url = stub.bio_url
                canonical.photo_url = stub.photo_url
                canonical.bio_summary = stub.bio_summary
                canonical.court = stub.court
                canonical.source_id = stub.source_id
                canonical.save(update_fields=[
                    "role", "status", "is_currently_seated",
                    "bio_url", "photo_url", "bio_summary",
                    "court", "source_id",
                ])
                stub.delete()
            merged += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. merged={merged}  skipped={skipped}"
            + ("  (DRY RUN -- nothing saved)" if dry_run else "")
        ))
