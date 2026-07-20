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

    def handle(self, *args, state, limit, dry_run, batch_size,
               recompute, min_confidence, **options):
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

        qs = Opinion.objects.exclude(raw_text="").select_related("court__state")
        if not recompute:
            qs = qs.filter(disposition="")
        if state:
            qs = qs.filter(court__state__code=state.upper())

        total = qs.count()
        if limit:
            total = min(total, limit)

        self.stdout.write(self.style.SUCCESS(
            f"Backfilling disposition for {total:,} opinions"
            + (f" in state {state.upper()}" if state else "")
            + ("." if not dry_run else " (DRY RUN; no DB writes).")
        ))

        to_update: list[Opinion] = []
        scanned = filled = no_match = 0
        corrected = cleared = unchanged = 0
        t0 = time.time()

        # iterator() so we don't load 49K rows into memory at once
        for op in qs.iterator(chunk_size=500):
            if limit and scanned >= limit:
                break
            scanned += 1

            if scanned % 2_000 == 0:
                elapsed = time.time() - t0
                rate = scanned / max(elapsed, 0.001)
                eta = (total - scanned) / max(rate, 0.001)
                self.stdout.write(
                    f"  scanned {scanned:>6,}/{total:,}  "
                    f"filled {filled:>5,}  no-match {no_match:>5,}  "
                    f"({rate:>3.0f}/s, eta {eta/60:.0f}min)",
                    ending="\n",
                )

            state_code = op.court.state_id
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

        if to_update and not dry_run:
            Opinion.objects.bulk_update(
                to_update,
                ["disposition", "disposition_bucket"],
            )

        elapsed = time.time() - t0
        summary = (
            f"\nDone in {elapsed/60:.1f} min. "
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
