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
import time
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from opinions.courtlistener import CourtListenerClient, CourtListenerError
from opinions.models import Court, Opinion
from opinions.utils import normalize_docket_number


logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 7

# DB write retry config -- NFSN's MariaDB drops idle SSL connections
# during the 30-60s CL rate-limit sleeps inside this command. Same
# pattern + tunings embed_opinions uses.
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP_SECONDS = 5


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
        # docket_id -> docket_number, so a run resolves each docket once.
        docket_number_cache: dict = {}

        try:
            # Pass --limit down so it bounds PAGINATION, not just processing.
            # Each cluster costs one fetch_opinion call per sub-opinion, and
            # CL's rate limiter answers a burst with multi-hour backoffs (a
            # 21-hour one is on record), so an unbounded listing is how a
            # catch-up run turns into a cooldown. Capping the listing keeps a
            # run's API budget predictable; re-run with the same --since to
            # continue.
            for cluster in client.iter_clusters_for_court(
                courtlistener_id,
                since=since_date.isoformat(),
                max_clusters=limit,
            ):
                clusters_seen += 1
                cluster_id = cluster.get("id")
                case_name = cluster.get("case_name") or ""
                date_filed = cluster.get("date_filed") or ""
                precedential_status = (cluster.get("precedential_status") or "").lower()
                absolute_url = cluster.get("absolute_url") or ""
                if absolute_url and not absolute_url.startswith("http"):
                    absolute_url = "https://www.courtlistener.com" + absolute_url

                # Concatenate plain_text from each sub-opinion. /search/?type=o
                # embeds opinion IDs under cluster["opinions"]; we fetch each
                # one directly because /opinions/?cluster=<id> is unreliably
                # slow in v4 (see CourtListenerClient.fetch_opinion docstring).
                raw_texts = []
                for op_meta in cluster.get("opinions") or []:
                    if not isinstance(op_meta, dict):
                        continue
                    op_id = op_meta.get("id")
                    if op_id is None:
                        continue
                    op_data = client.fetch_opinion(op_id)
                    plain = (op_data.get("plain_text") or "").strip()
                    if plain:
                        raw_texts.append(plain)
                raw_text = "\n\n".join(raw_texts)
                sha256 = (
                    hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                    if raw_text
                    else ""
                )

                # The real docket number is the site's URL key and what
                # paste-a-docket search matches on, so falling back to the
                # cluster id ("cl-12345") makes an opinion unreachable by the
                # only identifier a lawyer actually has. /search/ used to
                # denormalize docket_number; /clusters/ does not -- it gives
                # docket_id only -- so resolve it explicitly and treat the
                # synthetic id as a genuine last resort.
                #
                # Cached per run: many clusters share a docket (an appeal and
                # its later decision), and this is one extra request each
                # against a rate limiter that answers bursts with multi-hour
                # backoffs.
                raw_docket = cluster.get("docket_number") or ""
                if not raw_docket and cluster.get("docket_id") is not None:
                    d_id = cluster["docket_id"]
                    if d_id in docket_number_cache:
                        raw_docket = docket_number_cache[d_id]
                    else:
                        try:
                            raw_docket = (
                                client.fetch_docket(d_id).get("docket_number") or ""
                            )
                        except CourtListenerError as exc:
                            # Don't lose the opinion over a docket lookup --
                            # fall through to the synthetic id.
                            self.stderr.write(
                                f"    docket {d_id} lookup failed ({exc}); "
                                f"using synthetic case number"
                            )
                            raw_docket = ""
                        docket_number_cache[d_id] = raw_docket
                # Canonicalize to the dashed-uppercase form (CL stores docket
                # numbers undashed and occasionally lowercase).
                case_number = normalize_docket_number(
                    raw_docket or f"cl-{cluster_id}"
                )

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
                    # CL throttle sleeps of 30-60s give MariaDB's idle
                    # connection timeout time to drop our socket. The first
                    # write after a long sleep would then explode with
                    # OperationalError (2013, "Lost connection to MySQL
                    # server during query"). Retry-with-reconnect handles
                    # it: close the broken connection (Django auto-
                    # establishes on next use), wait a moment, retry. Same
                    # pattern embed_opinions uses for the same root cause.
                    from django.db import connection as _db, OperationalError as _DBErr
                    for db_attempt in range(1, DB_MAX_RETRIES + 1):
                        try:
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
                            break
                        except (_DBErr, BaseException) as db_exc:
                            # BaseException not Exception: NFSN's SSL socket
                            # raises KeyboardInterrupt on EINTR during long
                            # sleeps, which would otherwise terminate the
                            # whole run. Real SIGTERM/SIGKILL still take
                            # the process down because the outer signal
                            # arrives after this catch returns.
                            if db_attempt >= DB_MAX_RETRIES:
                                self.stderr.write(self.style.ERROR(
                                    f"    DB failed {DB_MAX_RETRIES}x for "
                                    f"{case_number}; skipping. "
                                    f"Last error: {type(db_exc).__name__}: {db_exc}"
                                ))
                                opinions_skipped += 1
                                break
                            self.stderr.write(self.style.WARNING(
                                f"    DB error (attempt {db_attempt}/{DB_MAX_RETRIES}) "
                                f"{type(db_exc).__name__}; reconnecting..."
                            ))
                            try:
                                _db.close()  # Django reconnects on next use
                            except BaseException:
                                pass
                            time.sleep(DB_RETRY_SLEEP_SECONDS)
                    else:
                        # Loop completed without break -- shouldn't happen
                        # given the inner break on success, but be defensive.
                        continue
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
