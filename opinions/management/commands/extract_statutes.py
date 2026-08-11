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

Resilient on NFSN: the scan walks the corpus in pk-ordered windows, each a
SEPARATE short query (``WHERE pk > last ORDER BY pk LIMIT N``), NOT one long
server-side stream. A single streaming ``.iterator()`` over a large state (AZ,
38K rows with 50-100KB ``raw_text`` each) gets its connection dropped mid-read
by NFSN's MariaDB (errno 2013). Windowed batches + retry-reconnect survive that
and make the sweep naturally resumable (a re-run skips opinions that already
have citations). The command's connection also lifts ``max_statement_time`` (the
25s web cap would kill the corpus-wide COUNT). Regex-only, no API calls.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from opinions.models import Court, Opinion, StatuteCitation
from opinions.parsing.statutes import extract_statutes


# Opinions per pk-window. Each window is its own short query, so this also
# bounds how much a single dropped-connection retry has to redo. 200 keeps
# peak RAM under ~20MB (raw_text runs 50-100KB) while staying network-efficient.
BATCH_SIZE = 200

# How many StatuteCitation rows to bulk-insert per write. MariaDB's default
# max_allowed_packet is 16MB; at ~200 bytes per row that's ~80K rows per
# packet -- 1000 leaves comfortable headroom.
BULK_INSERT_CHUNK = 1_000

# DB retry: NFSN's MariaDB sits behind an SSL connection that drops during long
# operations. On a drop we close the poisoned connection (Django reopens a
# clean one), re-lift the statement timeout, and retry. Catch BaseException --
# an SSL read EINTR'd by a signal surfaces as KeyboardInterrupt, which must be
# retryable here, not fatal (same reasoning as embed_opinions).
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP_SECONDS = 5


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
        parser.add_argument(
            "--max-runtime",
            type=int,
            default=0,
            help=(
                "Self-exit after N seconds at the next pk-window boundary. "
                "Cull-safe on NFSN when set to ~480 (~10-min wallclock cap). "
                "0 = unlimited (default). Pair with --min-id in a wrapper "
                "loop to progress across ticks."
            ),
        )
        parser.add_argument(
            "--min-id",
            type=int,
            default=0,
            help=(
                "Skip opinions with pk <= this value. The SQL-layer done-skip "
                "handles opinions already extracted, but NOT opinions scanned-"
                "then-found-empty (very common on LA, whose corpus is dense "
                "with short per-curiam orders that carry no statute cites). "
                "Without --min-id, every tick re-scans those same empties. "
                "Wrapper loops should scrape the printed 'resume with: "
                "--min-id N' line and feed it back."
            ),
        )

    # ---- DB resilience helpers -------------------------------------------

    def _lift_timeout(self):
        """Drop this connection's per-statement timeout (settings caps it at 25s
        for web safety; the corpus-wide COUNT legitimately runs longer)."""
        if connection.vendor == "mysql":
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION max_statement_time = 0")

    def _db_retry(self, fn):
        """Run ``fn`` with reconnect-and-retry on a dropped/interrupted
        connection. Returns fn()'s value; re-raises after DB_MAX_RETRIES."""
        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                return fn()
            except BaseException as exc:
                if attempt >= DB_MAX_RETRIES:
                    raise
                self.stderr.write(self.style.WARNING(
                    f"  DB error (attempt {attempt}/{DB_MAX_RETRIES}) "
                    f"{type(exc).__name__}: {exc}; reconnecting in "
                    f"{DB_RETRY_SLEEP_SECONDS}s..."
                ))
                try:
                    connection.close()
                except BaseException:
                    pass
                time.sleep(DB_RETRY_SLEEP_SECONDS)
                self._lift_timeout()

    # ---- main ------------------------------------------------------------

    def handle(self, *args, state, limit, force, dry_run, max_runtime, min_id, **options):
        state_code = state.upper()
        self._lift_timeout()

        # Pre-resolve court ids so the scan filters on the indexed court_id FK
        # instead of JOINing court->state on every row (CLAUDE.md gotcha).
        court_ids = list(
            Court.objects.filter(state__code=state_code).values_list("id", flat=True)
        )
        if not court_ids:
            self.stdout.write(self.style.WARNING(f"No courts for state {state_code!r}."))
            return

        def _count():
            base = Opinion.objects.filter(court_id__in=court_ids).exclude(raw_text="")
            if not force:
                base = base.exclude(pk__in=StatuteCitation.objects.values("opinion_id"))
            return base.count()
        total = self._db_retry(_count)
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
        last_pk = int(min_id or 0)
        next_report = 2_000
        t0 = time.time()

        def _flush_pending():
            nonlocal rows_created
            if not pending:
                return
            if not dry_run:
                rows = list(pending)

                def _do():
                    with transaction.atomic():
                        StatuteCitation.objects.bulk_create(
                            rows, batch_size=BULK_INSERT_CHUNK
                        )
                self._db_retry(_do)
            rows_created += len(pending)
            pending.clear()

        stopped_early = False
        while True:
            if limit and scanned >= limit:
                break
            if max_runtime and (time.time() - t0) >= max_runtime:
                # Self-exit under the NFSN cull. Next tick's SQL-layer exclude
                # picks up whatever this pass didn't reach.
                stopped_early = True
                break

            # Next pk-window as a fresh short query (lean: pk + raw_text only,
            # no model instances), with reconnect-on-drop.
            #
            # Exclude already-done opinions AT THE SQL LAYER, not just after
            # fetching. The pre-fix version pulled raw_text for every opinion
            # in the pk-window and skipped in Python via done_ids -- meaning
            # for a corpus like LA (341K, ~half already have rows from a prior
            # tick), each tick's 10-min NFSN cull hit before any new work got
            # done. Every tick died at the same PK, permanently stuck.
            # The subquery exclude uses the indexed FK on
            # opinions_statutecitation.opinion_id, so it's a fast anti-join,
            # not a full scan.
            def _fetch(_last=last_pk):
                qs = (
                    Opinion.objects.filter(court_id__in=court_ids, pk__gt=_last)
                    .exclude(raw_text="")
                )
                if not force:
                    qs = qs.exclude(
                        pk__in=StatuteCitation.objects.values("opinion_id")
                    )
                return list(
                    qs.order_by("pk").values_list("pk", "raw_text")[:BATCH_SIZE]
                )
            batch = self._db_retry(_fetch)
            if not batch:
                break
            last_pk = batch[-1][0]

            # Idempotency: skip opinions in this window that already have cites
            # (resume after an interrupted run), unless --force. One small
            # indexed query per window.
            if force:
                done_ids: set[int] = set()
            else:
                ids = [pk for pk, _ in batch]
                done_ids = self._db_retry(lambda _ids=ids: set(
                    StatuteCitation.objects.filter(opinion_id__in=_ids)
                    .values_list("opinion_id", flat=True)
                ))

            for pk, raw_text in batch:
                if limit and scanned >= limit:
                    break
                scanned += 1
                if pk in done_ids:
                    continue

                if force and not dry_run:
                    deleted, _ = self._db_retry(
                        lambda _pk=pk: StatuteCitation.objects.filter(
                            opinion_id=_pk).delete()
                    )
                    rows_deleted += deleted

                extractions = extract_statutes(state_code, raw_text)
                if extractions:
                    opinions_with_hits += 1
                    for e in extractions:
                        pending.append(StatuteCitation(
                            opinion_id=pk,
                            reference_slug=e.reference_slug,
                            reference_display=e.reference_display,
                            chapter=e.chapter,
                            section=e.section,
                            subdivision=e.subdivision,
                            text_offset=e.text_offset,
                        ))
                    if len(pending) >= BULK_INSERT_CHUNK:
                        _flush_pending()

            if scanned >= next_report:
                next_report += 2_000
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
        tag = " (stopped early on --max-runtime)" if stopped_early else ""
        self.stdout.write(self.style.SUCCESS(f"Done in {elapsed/60:.1f} min.{tag}"))
        self.stdout.write(
            f"  scanned:              {scanned:>7,}\n"
            f"  opinions with cites:  {opinions_with_hits:>7,}  "
            f"({100.0 * opinions_with_hits / max(scanned, 1):.1f}%)\n"
            f"  citation rows:        {rows_created:>7,}"
            + (f"\n  rows deleted (force): {rows_deleted:>7,}" if force else "")
            + ("\n  (DRY RUN -- nothing saved)" if dry_run else "")
        )
        # Resume marker for wrapper loops: prints the highest pk touched so a
        # next tick can pass --min-id and skip everything already scanned
        # (including opinions with no statute cites, which the SQL-layer
        # done-exclusion CAN'T mark as done).
        if last_pk:
            self.stdout.write(f"resume with: --min-id {last_pk}")
