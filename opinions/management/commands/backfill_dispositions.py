"""Backfill ``Opinion.disposition`` + ``disposition_bucket`` via the state parser.

The CL bulk loader uses ``bulk_create``, which bypasses ``Opinion.save()``
and therefore never runs the parser save-hook. As a result ~80% of the
60K MN opinions came in with an empty ``disposition`` field even when
the body text clearly states "Affirmed." or "Reversed and remanded."

This command finds those rows and runs the state parser on each one's
``raw_text``, populating ``disposition`` + ``disposition_bucket`` for
the ones where the parser finds a match. Opinions that already have a
disposition are skipped, so re-runs are idempotent and safe.

Cost: regex only, no API calls -- ~1-2 minutes for the full backfill.

Usage::

    python manage.py backfill_dispositions
    python manage.py backfill_dispositions --state MN
    python manage.py backfill_dispositions --limit 100 --dry-run
    python manage.py backfill_dispositions --state MN --batch-size 1000

Repairing rows a weaker parser already wrote::

    python manage.py backfill_dispositions --state NH \
        --recompute --min-confidence 0.8 --dry-run

``--recompute`` also visits rows that already have a disposition. A
stored value the parser now disagrees with is CORRECTED; one it can no
longer justify at ``--min-confidence`` is CLEARED. Clearing is
deliberate: a blank disposition is honest, while a stale wrong one
misstates what the court did. Always dry-run first -- the summary
reports corrected / cleared / unchanged separately.

After running, the editor still owns final review -- nothing here flips
``review_status`` past ``ai_only``. A disposition extracted by parser
stays AI-attributed until a human confirms via the admin "Mark as
human-reviewed" action.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Opinion
from opinions.utils import compute_disposition_bucket


class Command(BaseCommand):
    help = "Backfill Opinion.disposition via the state parser for empty rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state",
            default=None,
            help="Limit to this state code (e.g. 'MN'). Default: all live states.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N rows (smoke-test convenience).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute changes + print counts; don't save.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="bulk_update batch size (default 500).",
        )
        parser.add_argument(
            "--recompute",
            action="store_true",
            help=(
                "Also re-parse rows that ALREADY have a disposition, and "
                "correct them. Overwrites when the parser is at least "
                "--min-confidence and disagrees with what's stored; CLEARS "
                "the stored value when the parser no longer stands behind "
                "it. Use to repair rows written by a weaker parser."
            ),
        )
        parser.add_argument(
            "--min-confidence",
            type=float,
            default=0.0,
            help=(
                "Refuse to write a disposition the parser reports below this "
                "confidence (default 0.0 = write anything). Under --recompute "
                "this doubles as the threshold below which an existing value "
                "is cleared."
            ),
        )
        parser.add_argument(
            "--max-runtime",
            type=int,
            default=0,
            help=(
                "Self-exit after N seconds at the next pk-window boundary. "
                "Cull-safe on NFSN when set to ~480. 0 = unlimited."
            ),
        )
        parser.add_argument(
            "--min-id",
            type=int,
            default=0,
            help=(
                "Skip opinions with pk <= this value. The disposition=''"
                " filter can't mark no-match rows as done, so without "
                "--min-id a wrapper loop re-scans the same no-match tail "
                "every tick (the LA statutes/holdings lesson). Wrappers "
                "scrape the printed 'resume with:  --min-id N' trailer."
            ),
        )

    def handle(self, *args, state, limit, dry_run, batch_size,
               recompute, min_confidence, max_runtime, min_id, **options):
        # Local import: parse module loads the registry of state parsers
        # which has its own state model dependency. Importing inside the
        # command avoids a circular import at app load time.
        from opinions.parsing import parse as parse_opinion

        # Batch work: settings.py pins every connection to a 25s
        # max_statement_time, which is right for web requests and wrong
        # here -- the corpus-wide COUNT and the 500-row bulk_updates both
        # cross it under daytime contention (errno 1969).
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        # Base queryset filters, re-applied per pk-window. Do NOT use
        # qs.iterator() -- on Django's MySQL backend it buffers the whole
        # result client-side; a 341K-row LA scan at ~11KB avg raw_text is
        # ~3.7GB and trips NFSN's memory cull (rc=137). The original
        # comment here said "iterator() so we don't load 49K rows into
        # memory at once" -- it did exactly that anyway, just on the
        # client. pk-windowed short queries keep memory O(window).
        base_qs = Opinion.objects.exclude(raw_text="")
        if not recompute:
            base_qs = base_qs.filter(disposition="")
        # Court is a handful of rows; resolve court->state in Python so the
        # scan query never JOINs. A select_related("court") here made the
        # optimizer drive from the 6-row court table and collect EVERY
        # remaining matching row into a temp table + filesort per 500-row
        # batch (~3.8 min each, measured 2026-08-25); the identical WHERE
        # without the JOIN runs the PRIMARY range plan in 0.33s.
        from opinions.models import Court
        state_by_court = dict(Court.objects.values_list("id", "state_id"))
        if state:
            court_ids = [
                cid for cid, sc in state_by_court.items()
                if sc == state.upper()
            ]
            base_qs = base_qs.filter(court_id__in=court_ids)

        # The exact remaining-count costs ~112s on a 313K-row tail (the
        # disposition filter needs row data, so it walks the clustered
        # rows). It only feeds the banner + ETA, so a bounded tick skips
        # it -- a pass that self-exits on --max-runtime can't finish the
        # corpus anyway.
        if max_runtime:
            total = None
        else:
            total = (base_qs.filter(pk__gt=min_id) if min_id else base_qs).count()
            if limit:
                total = min(total, limit)

        self.stdout.write(self.style.SUCCESS(
            "Backfilling disposition for "
            + (f"{total:,} opinions" if total is not None else
               "opinions (count skipped under --max-runtime)")
            + (f" in state {state.upper()}" if state else "")
            + ("." if not dry_run else " (DRY RUN; no DB writes).")
        ))

        to_update: list[Opinion] = []
        scanned = filled = no_match = 0
        corrected = cleared = unchanged = 0
        last_pk = int(min_id or 0)
        stopped_early = False
        t0 = time.time()
        SCAN_BATCH = 500

        while True:
            if limit and scanned >= limit:
                break
            if max_runtime and (time.time() - t0) >= max_runtime:
                stopped_early = True
                break

            batch = list(
                base_qs.filter(pk__gt=last_pk)
                .order_by("pk")
                .only("id", "raw_text", "disposition", "court")[:SCAN_BATCH]
            )
            if not batch:
                break

            for op in batch:
                if limit and scanned >= limit:
                    break
                scanned += 1
                last_pk = op.pk

                state_code = state_by_court[op.court_id]
                result = parse_opinion(state_code, op.raw_text)
                confidence = (
                    result.confidence.get("disposition", 0.0) if result else 0.0
                )
                found = result.disposition if result else None
                # A parse we don't stand behind is treated as no parse at all.
                if found and confidence < min_confidence:
                    found = None

                if not found:
                    no_match += 1
                    # Under --recompute an existing value the parser can no
                    # longer justify is CLEARED. Blank is honest; a stale
                    # wrong disposition misstates what the court did.
                    if recompute and op.disposition:
                        op.disposition = ""
                        op.disposition_bucket = ""
                        to_update.append(op)
                        cleared += 1
                    else:
                        continue
                else:
                    new_disposition = found[:128]
                    if op.disposition == new_disposition:
                        unchanged += 1
                        continue
                    if op.disposition:
                        corrected += 1
                    else:
                        filled += 1
                    op.disposition = new_disposition
                    op.disposition_bucket = compute_disposition_bucket(new_disposition)
                    to_update.append(op)

                if len(to_update) >= batch_size and not dry_run:
                    Opinion.objects.bulk_update(
                        to_update,
                        ["disposition", "disposition_bucket"],
                    )
                    to_update.clear()

            if scanned // 2_000 > (scanned - len(batch)) // 2_000:
                elapsed = time.time() - t0
                rate = scanned / max(elapsed, 0.001)
                if total is not None:
                    eta = (total - scanned) / max(rate, 0.001)
                    progress = (
                        f"  scanned {scanned:>6,}/{total:,}  "
                        f"filled {filled:>5,}  no-match {no_match:>5,}  "
                        f"({rate:>3.0f}/s, eta {eta/60:.0f}min)"
                    )
                else:
                    progress = (
                        f"  scanned {scanned:>6,}  "
                        f"filled {filled:>5,}  no-match {no_match:>5,}  "
                        f"({rate:>3.0f}/s)"
                    )
                self.stdout.write(progress, ending="\n")

        if to_update and not dry_run:
            Opinion.objects.bulk_update(
                to_update,
                ["disposition", "disposition_bucket"],
            )

        elapsed = time.time() - t0
        tag = " (stopped early on --max-runtime)" if stopped_early else ""
        summary = (
            f"\nDone in {elapsed/60:.1f} min.{tag} "
            f"scanned={scanned:,} filled={filled:,} no-match={no_match:,}"
        )
        if recompute:
            summary += (
                f"\n  corrected={corrected:,} (had a disposition, parser disagreed)"
                f"\n  cleared={cleared:,} (parser no longer stands behind it)"
                f"\n  unchanged={unchanged:,}"
            )
        self.stdout.write(self.style.SUCCESS(
            summary + (" (dry-run; nothing saved)" if dry_run else "")
        ))
        # Resume marker for wrapper loops -- double space after "with:"
        # matches the statutes/holdings/citations wrapper grep convention.
        if last_pk:
            self.stdout.write(f"resume with:  --min-id {last_pk}")
