"""Re-fetch each opinion's ``plain_text`` from CourtListener to restore
page-break markers (``\\f`` form-feeds) that were lost from our stored
``raw_text``.

Motivation
==========
``format_opinion_text`` renders per-page deep-link anchors (``#page-N``)
by splitting ``raw_text`` on form-feed. CL's live API returns
``plain_text`` with form-feeds preserved (page boundaries from the
source PDF), but our historical bulk load stored raw_text without
them -- either CL's CSV export dropped them or the CSV parser did.
Either way, going back through the live API is the cheapest recovery.

Mechanics
=========
Our ``Opinion.courtlistener_id`` field holds a CL **cluster** id (not
an opinion id -- they're separate numbering spaces). To reach the
per-opinion ``plain_text`` we walk cluster -> sub_opinions -> pick the
"main" sub_opinion by type priority (010combined > 020lead >
010rehearing > 040dissent > ...) -> replace ``raw_text``.

Sanity gate
===========
NEVER overwrites raw_text with something that would clearly be worse:
    - CL returned empty plain_text -> skip (keeps existing content)
    - new length is <70% or >200% of old length -> skip + log
      (the 200% ceiling matters because CL sometimes has a longer
      "combined" opinion for the same cluster than what we originally
      stored; still refuse absurdly large deltas)
Existing raw_text is preserved on every skip -- this command can
only IMPROVE coverage of page anchors, never damage the corpus.

Cost
====
Each in-scope opinion costs one cluster fetch + one sub_opinion fetch
(both are single-record GETs at CL's authenticated rate limit of
~5000/hr). NH modern (1980+) = ~8,340 opinions = ~17K requests. Wire
speed ~4h; realistic 5-8h with 429 stalls.

Usage
=====
    # Smoke test 50 NH opinions:
    python manage.py backfill_pages_from_cl --state NH --limit 50

    # Full NH modern:
    python manage.py backfill_pages_from_cl --state NH --since 1980-01-01 \\
        --max-runtime 480 --min-id 0

    # NFSN cull-safe loop wrapper (bash):
    while true; do
      last=$(grep 'resume with: --min-id' /home/logs/nh-pages.log | tail -1 | \\
             sed -E 's/.*--min-id ([0-9]+).*/\\1/')
      python manage.py backfill_pages_from_cl --state NH --since 1980-01-01 \\
          --max-runtime 480 --min-id "${last:-0}" >> /home/logs/nh-pages.log 2>&1
      sleep 15
    done
"""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from opinions.courtlistener import CourtListenerClient, CourtListenerError
from opinions.models import Opinion, State

logger = logging.getLogger(__name__)

FF = "\f"

# Sub-opinion type priority when a cluster has multiple opinions
# (majority + concurrences + dissents). We want the MAIN opinion --
# the one whose text represents "the case." Lower = higher priority.
# Matches load_cl_bulk._opinion_type_priority in spirit.
_OPINION_TYPE_PRIORITY = {
    "010combined": 0,
    "020lead":     1,
    "030concurrence": 2,
    "040dissent":  3,
    "050addendum": 4,
    "060remittitur": 5,
    "070rehearing": 6,
    "080onthemerits": 7,
    "090onmotiontostrike": 8,
}


def _pick_main_sub_opinion(sub_ops: list[dict]) -> dict | None:
    """From a list of fetched sub-opinion dicts, pick the one whose
    plain_text should become our raw_text. Prefers the longest of the
    highest-priority type; falls back to the longest overall when no
    known type matches."""
    if not sub_ops:
        return None

    def _key(o):
        pt_len = len(o.get("plain_text") or "")
        prio = _OPINION_TYPE_PRIORITY.get(o.get("type") or "", 99)
        # (priority ascending, then length descending)
        return (prio, -pt_len)

    return sorted(sub_ops, key=_key)[0]


class Command(BaseCommand):
    help = (
        "Re-fetch opinion plain_text from CourtListener to restore "
        "\\f page-break markers lost from raw_text."
    )

    def add_arguments(self, parser):
        parser.add_argument("--state", required=True, help="State code (e.g. NH).")
        parser.add_argument(
            "--since", default=None,
            help="Only opinions with release_date >= YYYY-MM-DD (default: all).",
        )
        parser.add_argument(
            "--min-id", type=int, default=0,
            help="Resume after this Opinion.id (exclusive). Wrapper loops use this.",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Stop after N successful updates (0 = unlimited).",
        )
        parser.add_argument(
            "--max-runtime", type=int, default=0,
            help=(
                "Self-exit after N seconds at the next opinion boundary. "
                "Cull-safe on NFSN when set to ~480."
            ),
        )
        parser.add_argument(
            "--min-length-ratio", type=float, default=0.70,
            help="Refuse to overwrite when new/old length ratio is below this (default 0.70).",
        )
        parser.add_argument(
            "--max-length-ratio", type=float, default=2.00,
            help="Refuse to overwrite when new/old length ratio is above this (default 2.00).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Re-fetch even if raw_text already contains \\f markers.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what WOULD be updated without writing.",
        )

    def handle(self, *args, state, since, min_id, limit, max_runtime,
               min_length_ratio, max_length_ratio, force, dry_run, **_):
        # Lift the web-request statement-time cap for this batch command.
        if connection.vendor == "mysql":
            with connection.cursor() as c:
                c.execute("SET SESSION max_statement_time = 0")

        state_code = state.upper()
        try:
            state_obj = State.objects.get(pk=state_code)
        except State.DoesNotExist:
            raise CommandError(f"State {state_code!r} not seeded.")

        token = getattr(settings, "COURTLISTENER_TOKEN", "") or ""
        if not token:
            raise CommandError("COURTLISTENER_TOKEN is not set.")
        client = CourtListenerClient(token=token)

        # Pre-resolve court ids (see 'Pre-resolve court IDs to skip the join' gotcha)
        court_ids = list(state_obj.courts.values_list("id", flat=True))
        if not court_ids:
            raise CommandError(f"No courts registered for {state_code!r}.")

        # Base queryset: has cl_id, id > min_id, ordered by id for resume-stability
        qs = (
            Opinion.objects
            .filter(court_id__in=court_ids, id__gt=min_id)
            .exclude(courtlistener_id="")
            .exclude(courtlistener_id__isnull=True)
            .only("id", "case_number", "courtlistener_id", "raw_text", "release_date")
            .order_by("id")
        )
        if since:
            qs = qs.filter(release_date__gte=since)

        # Pull the id list eagerly so we don't hold a streaming cursor
        # across long API waits. Small (~8K rows) so memory is fine.
        candidate_ids = list(qs.values_list("id", flat=True))
        self.stdout.write(
            f"State={state_code} candidate opinions (id > {min_id}"
            + (f", since {since}" if since else "")
            + f"): {len(candidate_ids):,}"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN -- no DB writes."))

        t0 = time.time()
        updated = skipped_has_ff = skipped_empty = skipped_short = skipped_long = 0
        skipped_no_sub = errored = 0
        last_reported_id = min_id

        for pk in candidate_ids:
            if limit and updated >= limit:
                self.stdout.write(f"  hit --limit {limit}, stopping")
                break
            elapsed = time.time() - t0
            if max_runtime and elapsed >= max_runtime:
                self.stdout.write(
                    f"  hit --max-runtime {max_runtime}s at id={pk}, resume with: "
                    f"--min-id {last_reported_id}"
                )
                break

            # Re-load the row (may have changed since we listed ids).
            try:
                op = Opinion.objects.only(
                    "id", "case_number", "courtlistener_id", "raw_text"
                ).get(pk=pk)
            except Opinion.DoesNotExist:
                last_reported_id = pk
                continue

            old = op.raw_text or ""
            if FF in old and not force:
                skipped_has_ff += 1
                last_reported_id = pk
                continue

            try:
                cluster = client.fetch_cluster(op.courtlistener_id)
            except CourtListenerError as exc:
                errored += 1
                logger.warning("cluster fetch failed id=%s cl=%s: %s",
                               pk, op.courtlistener_id, exc)
                last_reported_id = pk
                continue

            sub_urls = cluster.get("sub_opinions") or []
            if not sub_urls:
                skipped_no_sub += 1
                last_reported_id = pk
                continue

            # Fetch each sub-opinion (usually 1, at most a handful).
            sub_ops = []
            for url in sub_urls:
                sub_id = url.rstrip("/").split("/")[-1]
                try:
                    sub_ops.append(client.fetch_opinion(sub_id))
                except CourtListenerError as exc:
                    logger.warning("sub_op fetch failed id=%s sub=%s: %s",
                                   pk, sub_id, exc)
            if not sub_ops:
                errored += 1
                last_reported_id = pk
                continue

            picked = _pick_main_sub_opinion(sub_ops)
            new = (picked.get("plain_text") or "") if picked else ""

            if not new.strip():
                skipped_empty += 1
                last_reported_id = pk
                continue

            # Sanity-check length ratio. Skip on either extreme.
            # (When old==0, treat as unbounded improvement -- fill it.)
            if old:
                ratio = len(new) / max(1, len(old))
                if ratio < min_length_ratio:
                    skipped_short += 1
                    logger.info(
                        "skip-short id=%s cn=%s ratio=%.2f (%d -> %d)",
                        pk, op.case_number, ratio, len(old), len(new),
                    )
                    last_reported_id = pk
                    continue
                if ratio > max_length_ratio:
                    skipped_long += 1
                    logger.info(
                        "skip-long id=%s cn=%s ratio=%.2f (%d -> %d)",
                        pk, op.case_number, ratio, len(old), len(new),
                    )
                    last_reported_id = pk
                    continue

            ff_count = new.count(FF)
            if dry_run:
                self.stdout.write(
                    f"  DRY id={pk} cn={op.case_number} "
                    f"{len(old)} -> {len(new)} chars, \\f={ff_count}"
                )
            else:
                # Truncate to same 5MB ceiling load_cl_bulk uses.
                Opinion.objects.filter(pk=pk).update(raw_text=new[:5_000_000])
            updated += 1
            last_reported_id = pk

            if updated % 25 == 0:
                self.stdout.write(
                    f"  progress: updated {updated}, elapsed {time.time()-t0:.0f}s, last id={pk}"
                )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. updated={updated}  skipped_has_ff={skipped_has_ff}  "
            f"skipped_empty={skipped_empty}  skipped_short={skipped_short}  "
            f"skipped_long={skipped_long}  skipped_no_sub={skipped_no_sub}  "
            f"errored={errored}  elapsed={time.time()-t0:.1f}s"
        ))
        if not (limit and updated >= limit):
            self.stdout.write(f"resume with: --min-id {last_reported_id}")
