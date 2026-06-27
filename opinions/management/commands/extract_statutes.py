"""Bulk-extract statute citations from opinion ``raw_text``.

Reads every Opinion in scope, runs the state's registered statute
extractor over its body, and writes the result into ``StatuteCitation``
rows. State is dispatched via ``opinions.parsing.statutes.extract_statutes``;
states without a registered extractor are silently skipped (returns []).

Currently supported: MN (Minn. Stat.), NH (RSA), AZ (A.R.S.).

Idempotent: by default, opinions that already have at least one
``StatuteCitation`` row are skipped. Pass ``--force`` to clear and re-extract
(useful after the regex is tightened in a follow-up release).

Usage::

    python manage.py extract_statutes              # full MN pass, idempotent
    python manage.py extract_statutes --state NH   # all NH opinions
    python manage.py extract_statutes --state AZ   # all AZ opinions
    python manage.py extract_statutes --dry-run    # count without writing
    python manage.py extract_statutes --limit 500  # smoke-test sweep
    python manage.py extract_statutes --force      # re-extract everything

Performance: regex-only, no API calls. Expected ~5-10 min for the full MN
corpus on dev hardware; production may be faster since MariaDB streams
``raw_text`` lazily.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from opinions.models import Court, Opinion, StatuteCitation
from opinions.parsing.statutes import extract_statutes


# How many opinions to fetch per ``iterator()`` chunk. Larger = fewer
# DB round-trips but more RAM held for raw_text strings (some opinions
# run 50-100KB). 200 keeps peak RAM under ~20MB while staying network-
# efficient.
ITER_CHUNK = 200

# How many StatuteCitation rows to bulk-insert per write. MariaDB's
# default max_allowed_packet is 16MB; at ~200 bytes per row that's
# ~80K rows per packet -- 1000 leaves comfortable headroom.
BULK_INSERT_CHUNK = 1_000


class Command(BaseCommand):
    help = (
        "Extract state-specific statute citations from opinion raw_text "
        "into StatuteCitation rows. Supports MN (Minn. Stat.), NH (RSA), AZ (A.R.S.)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--state",
            default="MN",
            help=(
                "USPS state code to scan (default MN). Supported: MN, NH, AZ. "
                "Each state's extractor lives in opinions/parsing/statutes_<code>.py."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N opinions (smoke-test convenience).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Re-extract even for opinions that already have citation rows. "
                "Existing rows for the matching opinions are deleted first."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute matches + counts but don't write or delete any rows.",
        )

    def handle(self, *args, state, limit, force, dry_run, **options):
        state_code = state.upper()

        # Batch command that legitimately runs long: it reads raw_text across
        # the whole state corpus. settings' init_command caps every connection
        # at max_statement_time=25 for web safety, but that kills the in-scope
        # COUNT on a large state -- AZ's 38K rows, each with a 50-100KB raw_text
        # read for the `exclude(raw_text="")` predicate, crossed 25s through
        # NFSN's tiny buffer pool (errno 1969). Lift the cap for THIS connection
        # only; web traffic keeps the 25s ceiling. (Same pattern as
        # embed_opinions / the long-migration gotcha in CLAUDE.md.)
        if connection.vendor == "mysql":
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION max_statement_time = 0")

        # Pre-resolve court ids so the scan filters on the indexed court_id FK
        # instead of JOINing court->state on every row (CLAUDE.md gotcha).
        court_ids = list(
            Court.objects.filter(state__code=state_code).values_list("id", flat=True)
        )

        qs = (
            Opinion.objects.filter(court_id__in=court_ids)
            .exclude(raw_text="")
            .order_by("pk")  # stable order for resumability
        )
        if not force:
            # `statute_citations__isnull=True` with distinct() works but
            # requires a LEFT JOIN on the citation table -- slow at 60K
            # rows. Subquery exclusion is faster: pre-compute the set of
            # opinion IDs that already have at least one citation and
            # exclude them at the SQL level.
            already_done = StatuteCitation.objects.values("opinion_id").distinct()
            qs = qs.exclude(pk__in=already_done)

        total = qs.count()
        if limit:
            total = min(total, limit)

        self.stdout.write(self.style.SUCCESS(
            f"Extracting statutes for {state_code}: "
            f"{total:,} opinion{'' if total == 1 else 's'} in scope"
            + (" (DRY RUN -- no writes)" if dry_run else "")
            + (" (FORCE -- existing rows will be cleared)" if force else "")
        ))

        scanned = 0
        opinions_with_hits = 0
        rows_created = 0
        rows_deleted = 0
        pending: list[StatuteCitation] = []
        t0 = time.time()

        def _flush_pending():
            """Bulk-insert any queued rows, respecting --dry-run."""
            nonlocal rows_created
            if not pending:
                return
            if not dry_run:
                StatuteCitation.objects.bulk_create(pending, batch_size=BULK_INSERT_CHUNK)
            rows_created += len(pending)
            pending.clear()

        for opinion in qs.iterator(chunk_size=ITER_CHUNK):
            if limit and scanned >= limit:
                break
            scanned += 1

            extractions = extract_statutes(state_code, opinion.raw_text)

            if force and not dry_run:
                # Clear existing rows so the new extractor pass replaces
                # them. Wrapped per-opinion in a transaction so a row's
                # delete + reinsert is atomic.
                with transaction.atomic():
                    deleted_count, _ = opinion.statute_citations.all().delete()
                    rows_deleted += deleted_count

            if extractions:
                opinions_with_hits += 1
                for e in extractions:
                    pending.append(StatuteCitation(
                        opinion=opinion,
                        reference_slug=e.reference_slug,
                        reference_display=e.reference_display,
                        chapter=e.chapter,
                        section=e.section,
                        subdivision=e.subdivision,
                        text_offset=e.text_offset,
                    ))
                if len(pending) >= BULK_INSERT_CHUNK:
                    _flush_pending()

            if scanned % 2_000 == 0:
                elapsed = time.time() - t0
                rate = scanned / max(elapsed, 0.001)
                eta = (total - scanned) / max(rate, 0.001)
                self.stdout.write(
                    f"  scanned {scanned:>6,}/{total:,}  "
                    f"hits={opinions_with_hits:>5,}  "
                    f"rows={rows_created + len(pending):>6,}  "
                    f"({rate:>4.0f}/s, eta {eta/60:.0f}min)"
                )

        _flush_pending()
        elapsed = time.time() - t0

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Done in {elapsed/60:.1f} min."))
        self.stdout.write(
            f"  scanned:              {scanned:>7,}\n"
            f"  opinions with cites:  {opinions_with_hits:>7,}  "
            f"({100.0 * opinions_with_hits / max(scanned, 1):.1f}%)\n"
            f"  citation rows:        {rows_created:>7,}"
            + (f"\n  rows deleted (force): {rows_deleted:>7,}" if force else "")
            + ("\n  (DRY RUN -- nothing saved)" if dry_run else "")
        )
