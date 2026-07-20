"""Populate ``Opinion.holding_summary`` by quoting the court, verbatim.

The deterministic counterpart to ``extract_holdings`` (which calls Claude
Haiku). This command generates nothing: it locates the sentence in which the
court states what it decided and stores that sentence exactly as written,
plus the court's own ``[¶N]`` paragraph markers for a pinpoint deep link.
Extraction logic lives in ``opinions/parsing/holdings.py``.

Measured coverage on the live corpus: **NH 86.6%, MN 68.2%, AZ 21.0%**.
AZ is low because its opinions use different conventions -- that is the same
root cause as its disposition gap (no AZ parser), and it gets fixed there,
not by spending money here.

Why this exists rather than just running the LLM version
--------------------------------------------------------
An LLM summary of an opinion that already says "We hold that X" is a lossy,
unverifiable paraphrase of a sentence we can quote exactly -- at roughly $88
per state at Haiku 4.5 pricing. Quoting exactly is the product posture (see
``/how-we-differ/``), and it keeps ML confined to the two places we disclose:
embeddings for semantic search, and tag suggestion. Running the LLM version
corpus-wide would make holdings a third ML surface for a worse artifact.

``holding_model`` records ``extractive-v1`` so that if an LLM is ever run
over the residual, the two methods stay auditable side by side per row.

Cost: regex only, no API calls.

Usage::

    python manage.py extract_holdings_text --state NH --dry-run
    python manage.py extract_holdings_text --state NH --limit 200
    python manage.py extract_holdings_text --state NH
    python manage.py extract_holdings_text --state NH --force

Idempotent: fills only empty ``holding_summary`` rows unless ``--force``.
A human-REVIEWED holding is NEVER overwritten, even with ``--force`` -- an
editor's judgment outranks the extractor's.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from opinions.models import Opinion
from opinions.parsing.holdings import summarize_holdings

EXTRACTOR_VERSION = "extractive-v1"


class Command(BaseCommand):
    help = "Populate Opinion.holding_summary with the court's own holding sentence."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state",
            default=None,
            help="Limit to this state code (e.g. 'NH'). Default: all states.",
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
            help="Compute + print counts; don't save.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Re-extract rows that already have a holding (e.g. after "
                "tuning the patterns). Never touches a human-REVIEWED one."
            ),
        )
        parser.add_argument(
            "--max-holdings",
            type=int,
            default=3,
            help="Max holding sentences to keep per opinion (default 3).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="bulk_update batch size (default 500).",
        )

    def handle(self, *args, state, limit, dry_run, force, max_holdings,
               batch_size, **options):
        # Batch work: settings.py pins every connection to a 25s
        # max_statement_time, which is right for the web tier and wrong here --
        # the corpus-wide COUNT and the bulk_updates both cross it under
        # daytime contention (errno 1969).
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        qs = Opinion.objects.exclude(raw_text="").select_related("court__state")
        if not force:
            qs = qs.filter(holding_summary="")
        else:
            # An editor's reviewed holding outranks the extractor -- always.
            qs = qs.exclude(
                holding_review_status=Opinion.ReviewStatus.REVIEWED
            )
        if state:
            qs = qs.filter(court__state__code=state.upper())

        total = qs.count()
        if limit:
            total = min(total, limit)

        self.stdout.write(self.style.SUCCESS(
            f"Extracting holdings for {total:,} opinions"
            + (f" in state {state.upper()}" if state else "")
            + ("." if not dry_run else " (DRY RUN; no DB writes).")
        ))

        to_update: list[Opinion] = []
        scanned = found = no_match = 0
        with_para = 0
        t0 = time.time()

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
                    f"found {found:>5,}  no-match {no_match:>5,}  "
                    f"({rate:>3.0f}/s, eta {eta/60:.0f}min)"
                )

            summary, paragraphs = summarize_holdings(
                op.raw_text, max_holdings=max_holdings
            )
            if not summary:
                no_match += 1
                continue

            op.holding_summary = summary
            op.holding_source_paras = paragraphs
            op.holding_model = EXTRACTOR_VERSION
            op.holding_extracted_at = timezone.now()
            to_update.append(op)
            found += 1
            if paragraphs:
                with_para += 1

            if len(to_update) >= batch_size and not dry_run:
                self._flush(to_update)

        if to_update and not dry_run:
            self._flush(to_update)

        elapsed = time.time() - t0
        pct = 100.0 * found / max(scanned, 1)
        self.stdout.write(self.style.SUCCESS(
            f"\nDone in {elapsed/60:.1f} min. "
            f"scanned={scanned:,} found={found:,} ({pct:.1f}%) "
            f"no-match={no_match:,}"
            f"\n  with a court-assigned ¶ anchor: {with_para:,}"
            + ("\n(dry-run; nothing saved)" if dry_run else "")
        ))

    @staticmethod
    def _flush(rows: list[Opinion]) -> None:
        """Persist a batch.

        Deliberately NOT ``Opinion.save()`` -- that re-runs the parser
        save-hook and would rewrite disposition/bucket as a side effect of a
        holdings run. Same reason ``embed_opinions`` uses a targeted update.
        Note ``holding_review_status`` is left at its default so every fresh
        extraction shows the amber "not yet human-reviewed" dot.
        """
        Opinion.objects.bulk_update(
            rows,
            [
                "holding_summary",
                "holding_source_paras",
                "holding_model",
                "holding_extracted_at",
            ],
        )
        rows.clear()
