"""Scrape the current MN judicial roster into Judge rows.

Usage::

    python manage.py scrape_judges mn              # full ingest
    python manage.py scrape_judges mn --dry-run    # parse + print, no writes

The scraper discovers each justice/judge's bio URL from mncourts.gov's
sitemap (auto-updates when new bios are published) and visits each one.
It marks any previously-seated Judge whose source_id is no longer in
the sitemap as ``is_currently_seated=False`` so the roster page never
shows former judges.
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from opinions.models import Court, Judge, State
from opinions.scrapers.mn_judges import MNJudgeScraper

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Scrape currently-seated MN appellate judges from mncourts.gov."

    def add_arguments(self, parser):
        parser.add_argument(
            "state_code",
            type=str,
            help="USPS 2-letter state code. Only 'mn' is supported in this version.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse the roster and print what would be written; no DB changes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after processing N judges (smoke-test convenience).",
        )

    def handle(self, *args, state_code, dry_run, limit, **options):
        state_code = (state_code or "").upper()
        if state_code != "MN":
            raise CommandError(
                f"Only MN is supported in this scraper version, got {state_code!r}."
            )

        try:
            state = State.objects.get(code=state_code)
        except State.DoesNotExist:
            raise CommandError(
                f"State {state_code} not found -- run migrations first."
            )

        try:
            sct_court = Court.objects.get(state=state, courtlistener_id="minn")
            coa_court = Court.objects.get(state=state, courtlistener_id="minnctapp")
        except Court.DoesNotExist as exc:
            raise CommandError(
                f"Required court row missing: {exc}. "
                "Run `manage.py migrate` to apply the seed migrations."
            )

        court_by_kind = {"SUPREME": sct_court, "APPEALS": coa_court}

        scraper = MNJudgeScraper()

        self.stdout.write("Discovering roster from mncourts.gov sitemap...")
        sct_urls, coa_urls = scraper.discover_bio_urls()
        total = len(sct_urls) + len(coa_urls)
        self.stdout.write(
            f"  found {len(sct_urls)} Supreme Court + {len(coa_urls)} Court of Appeals = {total} bios"
        )

        # Fetch all bios. Sleep between requests so the cron is polite
        # to upstream -- 26 fetches * 0.4s = ~10s of pure waiting, fine.
        from opinions.scrapers.mn_judges import SLEEP_BETWEEN_FETCHES

        scraped = []
        all_urls = (
            [(u, "SUPREME") for u in sct_urls]
            + [(u, "APPEALS") for u in coa_urls]
        )
        for url, court_kind in all_urls:
            tag = "SCt" if court_kind == "SUPREME" else "COA"
            self.stdout.write(f"  [{tag}] {url.rsplit('/', 1)[-1]}")
            row = scraper.fetch_bio(url, court_kind=court_kind)
            if row is not None:
                scraped.append(row)
            if limit and len(scraped) >= limit:
                break
            scraper._sleep(SLEEP_BETWEEN_FETCHES)

        # Persist.
        created = 0
        updated = 0
        current_source_ids: set[str] = set()

        for row in scraped:
            current_source_ids.add(row.source_id)
            if dry_run:
                self.stdout.write(
                    f"    DRY: {row.full_name} | role={row.role} | "
                    f"court_kind={row.court_kind} | "
                    f"appointed={row.appointment_date} | "
                    f"photo={'yes' if row.photo_url else 'no'} | "
                    f"bio_len={len(row.bio_summary)}"
                )
                continue

            court = court_by_kind.get(row.court_kind)
            slug = MNJudgeScraper.django_slug_for(row.source_id)

            # Split fields by who owns them:
            # - Scraper-authoritative -- always re-synced on each run because
            #   they reflect the official upstream roster.
            # - User-editable on first creation only -- the user has full
            #   control via admin and the scraper never clobbers their work.
            obj, was_created = Judge.objects.get_or_create(
                state=state,
                source_id=row.source_id,
                defaults={
                    "court": court,
                    "full_name": row.full_name,
                    "slug": slug,
                    "role": row.role,
                    "status": Judge.Status.ACTIVE,
                    "is_currently_seated": True,
                    "bio_url": row.bio_url,
                    "photo_url": row.photo_url,
                    # Seeded on first run; never touched again. User-editable.
                    "bio_summary": row.bio_summary,
                    "appointment_date": row.appointment_date,
                },
            )
            if was_created:
                created += 1
            else:
                # Resync only the scraper-authoritative fields. bio_summary
                # and appointment_date stay whatever the user (or the first
                # scrape) set them to.
                obj.court = court
                obj.full_name = row.full_name
                obj.slug = slug
                obj.role = row.role
                obj.is_currently_seated = True
                obj.bio_url = row.bio_url
                obj.photo_url = row.photo_url
                obj.save(update_fields=[
                    "court", "full_name", "slug", "role",
                    "is_currently_seated", "bio_url", "photo_url",
                ])
                updated += 1

        # Anyone we previously had as seated but who isn't in today's sitemap
        # has left the bench (retired, elevated, etc). Mark them so the
        # roster page stays current without dropping the historical row.
        stale_marked = 0
        if not dry_run and current_source_ids:
            stale_qs = Judge.objects.filter(
                state=state,
                is_currently_seated=True,
            ).exclude(source_id__in=current_source_ids)
            stale_marked = stale_qs.update(is_currently_seated=False)

        self.stdout.write(self.style.SUCCESS(
            f"Done. scraped={len(scraped)} created={created} updated={updated} "
            f"marked-no-longer-seated={stale_marked}"
            + (" (dry-run; no DB changes)" if dry_run else "")
        ))
