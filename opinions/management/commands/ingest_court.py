"""Ingest opinions from a CourtListener court into the local DB.

Usage:
    python manage.py ingest_court <courtlistener_id> [--since YYYY-MM-DD]
                                                     [--limit N]
                                                     [--dry-run]

Example:
    python manage.py ingest_court minn --since 2026-05-01 --limit 5

For v0.1 this stores cluster-level metadata + concatenated opinion text on
the Opinion row. Judge/panel ingestion is deliberately deferred -- mapping
CourtListener person URLs to Judge rows requires a separate API call per
person and would blow through the 125/day rate limit on real backfills.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from opinions.courtlistener import CourtListenerClient, CourtListenerError
from opinions.models import Court, Opinion


logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 7


class Command(BaseCommand):
    help = "Ingest recent opinions for a CourtListener court into the DB."

    def add_arguments(self, parser):
        parser.add_argument(
            "courtlistener_id",
            help="CourtListener court identifier (e.g. 'minn', 'minnctapp').",
        )
        parser.add_argument(
            "--since",
            help=(
                "ISO date (YYYY-MM-DD). Only clusters with date_filed on or "
                "after this date are fetched. Default: 7 days ago."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after processing N clusters (useful for smoke tests).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be ingested without writing to the DB.",
        )

    def handle(self, *args, courtlistener_id, since, limit, dry_run, **options):
        token = getattr(settings, "COURTLISTENER_TOKEN", "") or ""
        if not token:
            raise CommandError(
                "COURTLISTENER_TOKEN is not set. Add it to .env "
                "(see .env.example)."
            )

        try:
            court = Court.objects.get(courtlistener_id=courtlistener_id)
        except Court.DoesNotExist:
            raise CommandError(
                f"No Court row with courtlistener_id={courtlistener_id!r}. "
                "Add a data migration to seed it first."
            )

        if since:
            since_date = parse_date(since)
            if since_date is None:
                raise CommandError(
                    f"--since must be ISO YYYY-MM-DD; got {since!r}"
                )
        else:
            since_date = date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)

        self.stdout.write(
            f"Ingesting {court.name} ({courtlistener_id}) since {since_date}"
            + (" (dry-run)" if dry_run else "")
        )

        client = CourtListenerClient(token=token)

        clusters_seen = 0
        opinions_created = 0
        opinions_updated = 0
        opinions_skipped = 0

        try:
            for cluster in client.iter_clusters_for_court(
                courtlistener_id, since=since_date.isoformat()
            ):
                clusters_seen += 1
                cluster_id = cluster.get("id")
                case_name = cluster.get("case_name") or ""
                date_filed = cluster.get("date_filed") or ""
                precedential_status = (cluster.get("precedential_status") or "").lower()
                absolute_url = cluster.get("absolute_url") or ""
                if absolute_url and not absolute_url.startswith("http"):
                    absolute_url = "https://www.courtlistener.com" + absolute_url

                # Concatenate plain_text from each sub-opinion in the cluster
                # (majority + any concurrences/dissents).
                raw_texts = []
                for opinion in client.fetch_opinions_for_cluster(cluster_id):
                    plain = (opinion.get("plain_text") or "").strip()
                    if plain:
                        raw_texts.append(plain)
                raw_text = "\n\n".join(raw_texts)
                sha256 = (
                    hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                    if raw_text
                    else ""
                )

                # cluster has a docket URL we'd need to fetch for the real
                # docket_number; for v0.1 we fall back to the cluster id when
                # the cluster doesn't denormalize docket_number directly.
                case_number = cluster.get("docket_number") or f"cl-{cluster_id}"

                self.stdout.write(
                    f"  [{clusters_seen}] {date_filed} {case_number} | "
                    f"{precedential_status or '?'} | {case_name[:60]}"
                )

                if dry_run:
                    opinions_skipped += 1
                    if limit and clusters_seen >= limit:
                        self.stdout.write(f"  reached --limit {limit}, stopping.")
                        break
                    continue

                parsed_date = parse_date(date_filed) if date_filed else None
                if parsed_date is None:
                    self.stderr.write("    skipping: missing/invalid date_filed")
                    opinions_skipped += 1
                else:
                    _, created = Opinion.objects.update_or_create(
                        court=court,
                        case_number=case_number,
                        defaults={
                            "title": case_name,
                            "release_date": parsed_date,
                            "is_precedential": precedential_status == "published",
                            "raw_text": raw_text,
                            "source_url": absolute_url,
                            "courtlistener_id": str(cluster_id),
                            "sha256": sha256,
                        },
                    )
                    if created:
                        opinions_created += 1
                    else:
                        opinions_updated += 1

                if limit and clusters_seen >= limit:
                    self.stdout.write(f"  reached --limit {limit}, stopping.")
                    break
        except CourtListenerError as exc:
            raise CommandError(f"CourtListener error: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(
            f"Done. clusters_seen={clusters_seen} "
            f"created={opinions_created} updated={opinions_updated} "
            f"skipped={opinions_skipped}"
        ))
