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
from opinions.scrapers.az_supreme import AZSupremeJudgeScraper
from opinions.scrapers.mn_judges import MNJudgeScraper

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Scrape currently-seated appellate judges from the state's judicial site."

    def add_arguments(self, parser):
        parser.add_argument(
            "state_code",
            type=str,
            help="USPS 2-letter state code. Supported: 'mn', 'az' (Supreme Court only).",
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
        if state_code not in ("MN", "AZ"):
            raise CommandError(
                f"Supported state_codes: 'MN', 'AZ'. Got {state_code!r}."
            )

        try:
            state = State.objects.get(code=state_code)
        except State.DoesNotExist:
            raise CommandError(
                f"State {state_code} not found -- run migrations first."
            )

        if state_code == "AZ":
            return self._handle_az(state, dry_run, limit)

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

    def _handle_az(self, state, dry_run, limit):
        """Scrape AZ Supreme Court roster and reconcile against existing
        AZ Judge rows. Unlike MN (where the roster scraper is the source
        of truth and rows are uniquely keyed by source_id), AZ has 120+
        Judge rows already populated from the CL bulk dump's people
        table -- we don't want to create duplicates. So persistence is:

        1. Try to match each scraped justice against an existing AZ
           Judge row by last-name (state=AZ, court=ariz). If found,
           UPDATE that row's bio/photo/role/source_id in place.
        2. If not found, CREATE a new Judge row. Slug derived from full
           name; conflicts auto-suffixed.

        Last-name matching mirrors what resolve_judges does for byline
        resolution. Only the Supreme Court is in scope for this scraper
        version; AZ Court of Appeals Div 1 + Div 2 sit on a different
        DNN host and would need separate scrapers.
        """
        try:
            supreme_court = Court.objects.get(state=state, courtlistener_id="ariz")
        except Court.DoesNotExist:
            raise CommandError(
                "Required Court row missing for AZ Supreme. Run migrations."
            )

        from django.utils.text import slugify
        from collections import defaultdict

        scraper = AZSupremeJudgeScraper()
        self.stdout.write("Fetching AZ Supreme Court roster from azcourts.gov...")
        scraped = scraper.fetch_roster()
        self.stdout.write(f"  found {len(scraped)} justices on the listing page")
        if limit:
            scraped = scraped[:limit]

        # Build a last-name -> [Judge,...] lookup over existing AZ Supreme
        # Court rows so we can match-and-update instead of duplicating.
        last_name_map: dict[str, list[Judge]] = defaultdict(list)
        existing = Judge.objects.filter(state=state, court=supreme_court)
        for j in existing:
            tokens = (j.full_name or "").strip().split()
            if not tokens:
                continue
            ln = tokens[-1].lower().strip(",.;:")
            last_name_map[ln].append(j)

        created = matched_updated = ambiguous_skipped = 0
        for row in scraped:
            tokens = row.full_name.split()
            if not tokens:
                continue
            last_lower = tokens[-1].lower().strip(",.;:")
            matches = last_name_map.get(last_lower, [])

            if dry_run:
                tag = (
                    "MATCH(unique)" if len(matches) == 1
                    else f"MATCH(amb x{len(matches)})" if matches
                    else "NEW"
                )
                self.stdout.write(
                    f"    DRY [{tag}] {row.full_name} -- bio_len={len(row.bio_summary)} "
                    f"photo={'yes' if row.photo_url else 'no'}"
                )
                continue

            if len(matches) > 1:
                ambiguous_skipped += 1
                self.stderr.write(self.style.WARNING(
                    f"  ambiguous match for {row.full_name!r} "
                    f"({len(matches)} existing Judges share last name); skipping"
                ))
                continue

            if len(matches) == 1:
                # Update the existing CL-bulk-loaded row in place. Don't
                # touch slug (URLs would break) or full_name (CL bulk
                # form may be canonical; we won't second-guess).
                j = matches[0]
                j.court = supreme_court
                j.role = row.role
                j.status = Judge.Status.ACTIVE
                j.is_currently_seated = True
                j.bio_url = row.bio_url
                j.photo_url = row.photo_url
                j.bio_summary = row.bio_summary
                j.source_id = row.source_id
                j.save(update_fields=[
                    "court", "role", "status", "is_currently_seated",
                    "bio_url", "photo_url", "bio_summary", "source_id",
                ])
                matched_updated += 1
            else:
                # No existing row -- create one. Slug from full name with
                # numeric suffix on collision.
                base_slug = slugify(row.full_name) or row.source_id
                slug = base_slug
                n = 2
                while Judge.objects.filter(state=state, slug=slug).exists():
                    slug = f"{base_slug}-{n}"
                    n += 1
                Judge.objects.create(
                    state=state,
                    court=supreme_court,
                    full_name=row.full_name,
                    slug=slug,
                    role=row.role,
                    status=Judge.Status.ACTIVE,
                    is_currently_seated=True,
                    bio_url=row.bio_url,
                    photo_url=row.photo_url,
                    bio_summary=row.bio_summary,
                    source_id=row.source_id,
                )
                # Index the new row so subsequent scraped justices with the
                # same last name (sibling first-names) match correctly.
                last_name_map[last_lower].append(_LazyJudge(slug))
                created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. scraped={len(scraped)} matched-updated={matched_updated} "
            f"created={created} ambiguous-skipped={ambiguous_skipped}"
            + (" (dry-run; no DB changes)" if dry_run else "")
        ))


class _LazyJudge:
    """Sentinel used only to make the last_name_map count include
    just-created rows so subsequent matches don't double-create. We
    don't need the Judge object itself, just a placeholder for length
    counting in the ambiguity check."""
    __slots__ = ("slug",)

    def __init__(self, slug: str) -> None:
        self.slug = slug
