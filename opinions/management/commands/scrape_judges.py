"""Scrape a state's current judicial roster into Judge rows.

Usage::

    python manage.py scrape_judges mn              # full ingest
    python manage.py scrape_judges mn --dry-run    # parse + print, no writes
    python manage.py scrape_judges nh              # NH ingest (5 SCt justices)

Per-state scraper modules live in ``opinions/scrapers/``; this command
dispatches to the right one via ``SCRAPER_REGISTRY``. Each scraper exposes
the same ``ScrapedJudge`` dataclass + ``scrape_all()`` interface, so this
command stays state-agnostic past the registry lookup.

Persistence rules (same as the original MN command):

- ``get_or_create`` on ``(state, source_id)``. New rows get scraper data
  on every field. Existing rows have scraper-authoritative fields
  resynced (court, full_name, slug, role, is_currently_seated, bio_url,
  photo_url) but user-editable fields (bio_summary, appointment_date)
  are NEVER overwritten -- the editor's hand-curated text wins.
- Any previously-seated judge in this state whose source_id is no longer
  present in today's scrape gets ``is_currently_seated=False`` so the
  roster page stays current without losing the row.
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from opinions.models import Court, Judge, State
from opinions.scrapers.mn_judges import MNJudgeScraper
from opinions.scrapers.nh_judges import NHJudgeScraper

logger = logging.getLogger(__name__)


# State code -> scraper class. New states slot in here once their
# scraper module ships. Every scraper class implements:
#   - ``scrape_all()`` -> list[ScrapedJudge]
#   - ``django_slug_for(source_id)`` -> str
#   - ``_sleep`` callable + ``SLEEP_BETWEEN_FETCHES``-like cadence
SCRAPER_REGISTRY = {
    "MN": MNJudgeScraper,
    "NH": NHJudgeScraper,
}


class Command(BaseCommand):
    help = "Scrape currently-seated appellate judges from a state's judicial website."

    def add_arguments(self, parser):
        parser.add_argument(
            "state_code",
            type=str,
            help=f"USPS 2-letter state code. Supported: {sorted(SCRAPER_REGISTRY)}.",
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
        scraper_cls = SCRAPER_REGISTRY.get(state_code)
        if scraper_cls is None:
            raise CommandError(
                f"No scraper registered for state {state_code!r}. "
                f"Supported: {sorted(SCRAPER_REGISTRY)}."
            )

        try:
            state = State.objects.get(code=state_code)
        except State.DoesNotExist:
            raise CommandError(
                f"State {state_code} not found -- run migrations first."
            )

        # Build court_by_kind dynamically from what exists in the DB for
        # this state. MN has both SUPREME + APPEALS; NH has only SUPREME.
        # Lets future states with different court structures slot in
        # without touching this command.
        courts_for_state = {c.level: c for c in Court.objects.filter(state=state)}
        if not courts_for_state:
            raise CommandError(
                f"No Court rows seeded for state {state_code}. "
                "Run `manage.py migrate` to apply the seed migrations."
            )
        court_by_kind = {
            "SUPREME": courts_for_state.get("SUPREME"),
            "APPEALS": courts_for_state.get("APPEALS"),
        }
        self.stdout.write(
            f"Courts in scope for {state_code}: "
            + ", ".join(
                f"{k}={v.courtlistener_id}" for k, v in court_by_kind.items() if v
            )
        )

        scraper = scraper_cls()
        self.stdout.write(f"Scraping {state_code} roster...")
        scraped = scraper.scrape_all()
        self.stdout.write(f"  scraped {len(scraped)} judge(s)")

        if limit:
            scraped = scraped[:limit]

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
            slug = scraper_cls.django_slug_for(row.source_id)

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
                    # User-editable; seeded on first run, then never
                    # clobbered by subsequent scrapes.
                    "bio_summary": row.bio_summary,
                    "appointment_date": row.appointment_date,
                },
            )
            if was_created:
                created += 1
            else:
                # Resync only scraper-authoritative fields.
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
