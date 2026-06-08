"""Score each opinion against every embedded Tag, write TagSuggestion rows.

For every opinion that:
  - has an ``embedding`` (i.e. was processed by ``embed_opinions``),
  - has no existing ``TagSuggestion`` rows (idempotent skip),

compute cosine similarity to all embedded ``Tag`` rows. The top
``TAG_SUGGESTION_TOP_N`` matches above ``TAG_SUGGESTION_REVIEW_THRESHOLD``
become ``TagSuggestion`` rows. Anything above
``TAG_SUGGESTION_AUTO_APPLY_THRESHOLD`` gets the tag attached to the
opinion automatically (status=AUTO_APPLIED) so the maintainer doesn't
have to click through a guaranteed-yes.

Implementation: load every Tag.embedding into a single (n_tags, 1024)
numpy matrix once, normalize. Stream opinions in chunks of N, decode
each batch's vectors via ``Vec_ToText``, normalize, then a single
matmul gives a (chunk, n_tags) similarity matrix. ~50ms per chunk
including DB roundtrip; ~5-10 min total for the 60K-opinion corpus.

Local SQLite dev no-ops because the opinion VECTOR column doesn't
exist there. Run this command on the box that has the production
MariaDB connection.

Idempotent: re-runs skip opinions that already have any TagSuggestion
row, so cron can run nightly without duplicating work. To re-score a
specific opinion (e.g. after editing tags), delete its TagSuggestion
rows first and re-run.

Usage::

    python manage.py suggest_tags                # full corpus pass
    python manage.py suggest_tags --limit 500    # smoke test
    python manage.py suggest_tags --dry-run      # score but don't write
    python manage.py suggest_tags --rescore-all  # ignore the idempotent skip
"""
from __future__ import annotations

import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from opinions.models import Opinion, Tag, TagSuggestion


# Chunk size for the opinion fetch loop. Each opinion's embedding text
# from Vec_ToText is ~16KB (JSON list of 1024 floats). 500 rows per
# chunk = ~8MB held in memory at peak; numpy matmul on (500, 1024) x
# (1024, n_tags) is microseconds. Larger chunks = slightly less DB
# overhead but more peak RAM; this is the right balance for NFSN's
# shared-host memory budget.
OPINION_CHUNK_SIZE = 500


class Command(BaseCommand):
    help = "Score opinions against tags via cosine similarity; write TagSuggestion rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N opinions (smoke-test convenience).",
        )
        parser.add_argument(
            "--rescore-all",
            action="store_true",
            help=(
                "Ignore the idempotent skip and re-score every embedded opinion. "
                "Existing TagSuggestion rows are kept (unique_together prevents "
                "duplicates); new tags/changed thresholds will surface new rows."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute suggestions + counts but don't write or auto-apply tags.",
        )

    def handle(self, *args, limit, rescore_all, dry_run, **options):
        if connection.vendor != "mysql":
            raise CommandError(
                f"suggest_tags requires MariaDB / MySQL "
                f"(got {connection.vendor!r}). The Opinion.embedding VECTOR column "
                "doesn't exist on SQLite. Run this command on production."
            )

        try:
            import numpy as np
        except ImportError:
            raise CommandError(
                "numpy is required for cosine math. Install with: pip install numpy"
            )

        review_threshold = getattr(settings, "TAG_SUGGESTION_REVIEW_THRESHOLD", 0.65)
        auto_apply_threshold = getattr(settings, "TAG_SUGGESTION_AUTO_APPLY_THRESHOLD", 0.85)
        top_n = getattr(settings, "TAG_SUGGESTION_TOP_N", 5)

        if not (0.0 <= review_threshold <= auto_apply_threshold <= 1.0):
            raise CommandError(
                f"Thresholds out of order: review={review_threshold}, "
                f"auto_apply={auto_apply_threshold}. Need 0 <= review <= auto_apply <= 1."
            )

        # (1) Load all embedded tags into a single matrix.
        tags = list(Tag.objects.exclude(embedding__isnull=True).order_by("slug"))
        if not tags:
            raise CommandError(
                "No tags have embeddings yet. Run `python manage.py embed_tags` first."
            )

        tag_ids = [t.pk for t in tags]
        tag_slugs = [t.slug for t in tags]
        tag_matrix = np.array([t.embedding for t in tags], dtype=np.float32)  # (n_tags, 1024)
        # Normalize each row so cosine = dot product
        tag_norms = np.linalg.norm(tag_matrix, axis=1, keepdims=True)
        # Guard against zero-norm vectors (shouldn't happen; Voyage always
        # returns unit-ish vectors, but defensive).
        tag_norms = np.where(tag_norms == 0, 1.0, tag_norms)
        tag_matrix = tag_matrix / tag_norms
        tag_matrix_t = tag_matrix.T  # (1024, n_tags) -- transpose once for matmul

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(tags)} embedded tags. "
            f"thresholds: review={review_threshold}  auto={auto_apply_threshold}  "
            f"top_n={top_n}"
        ))

        # (2) Build the opinion candidate set. Embedded + (optionally) no
        # existing suggestions.
        with connection.cursor() as cursor:
            base_where = "embedding IS NOT NULL"
            if not rescore_all:
                # Subquery exclusion: opinions that already have any
                # TagSuggestion row. Faster than a LEFT JOIN at 60K scale.
                base_where += (
                    " AND id NOT IN ("
                    "  SELECT opinion_id FROM opinions_tagsuggestion"
                    ")"
                )
            cursor.execute(
                f"SELECT COUNT(*) FROM opinions_opinion WHERE {base_where}"
            )
            total_in_scope = cursor.fetchone()[0]

        if limit:
            total_in_scope = min(total_in_scope, limit)

        if total_in_scope == 0:
            self.stdout.write(self.style.SUCCESS(
                "All embedded opinions already have suggestions. Nothing to do."
            ))
            return

        self.stdout.write(
            f"Scoring {total_in_scope:,} opinion{'' if total_in_scope == 1 else 's'}"
            + (" (DRY RUN -- no writes)" if dry_run else "")
            + (" (RESCORE ALL -- ignoring idempotent skip)" if rescore_all else "")
        )

        scanned = 0
        suggestions_pending = 0
        suggestions_auto = 0
        tags_auto_applied = 0
        run_started = time.time()
        last_opinion_id = 0  # for keyset pagination

        while scanned < total_in_scope:
            # Pull next chunk of opinions with their VECTOR-decoded embeddings.
            # Keyset pagination on id beats LIMIT/OFFSET as id grows.
            remaining = total_in_scope - scanned
            chunk_target = min(OPINION_CHUNK_SIZE, remaining)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT id, Vec_ToText(embedding)
                    FROM opinions_opinion
                    WHERE {base_where}
                      AND id > %s
                    ORDER BY id
                    LIMIT %s
                    """,
                    [last_opinion_id, chunk_target],
                )
                rows = cursor.fetchall()
            if not rows:
                break

            # Parse embeddings -> matrix (chunk, 1024)
            opinion_ids = [r[0] for r in rows]
            try:
                vecs = np.array(
                    [json.loads(r[1]) for r in rows],
                    dtype=np.float32,
                )
            except (TypeError, ValueError) as exc:
                # One bad row would break the whole batch. Filter + log
                # rather than crash -- the bad row gets skipped, suggest_tags
                # can be re-run after the editor fixes it.
                self.stderr.write(self.style.WARNING(
                    f"  bad embedding JSON in chunk starting opinion {opinion_ids[0]}: "
                    f"{exc}. Skipping the chunk."
                ))
                last_opinion_id = opinion_ids[-1]
                scanned += len(rows)
                continue

            # Normalize each row, then matmul with tag matrix transpose:
            # similarity[i, j] = cos(opinion_i, tag_j)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            vecs = vecs / norms
            sims = vecs @ tag_matrix_t  # (chunk, n_tags)

            # For each opinion: take top-N tag indices, filter by threshold,
            # build TagSuggestion rows.
            now = timezone.now()
            for row_idx, opinion_id in enumerate(opinion_ids):
                row_sims = sims[row_idx]
                # argsort ascending; take last top_n entries
                top_indices = np.argsort(row_sims)[-top_n:][::-1]
                for tag_idx in top_indices:
                    score = float(row_sims[tag_idx])
                    if score < review_threshold:
                        continue  # below review band -- don't surface
                    tag_id = tag_ids[tag_idx]
                    if score >= auto_apply_threshold:
                        status = TagSuggestion.Status.AUTO_APPLIED
                        suggestions_auto += 1
                    else:
                        status = TagSuggestion.Status.PENDING
                        suggestions_pending += 1
                    if dry_run:
                        continue
                    # Idempotent insert -- get_or_create skips if (opinion, tag)
                    # already exists. Use atomic per-opinion so a mid-batch
                    # crash doesn't leave half a row set.
                    obj, created = TagSuggestion.objects.get_or_create(
                        opinion_id=opinion_id,
                        tag_id=tag_id,
                        defaults={
                            "confidence": score,
                            "status": status,
                            "reviewed_at": now if status == TagSuggestion.Status.AUTO_APPLIED else None,
                            "reviewed_by": "auto-applied" if status == TagSuggestion.Status.AUTO_APPLIED else "",
                        },
                    )
                    if created and status == TagSuggestion.Status.AUTO_APPLIED:
                        # Add tag to opinion's tag set (the actual editorial
                        # surface change). The M2M through table will dedup
                        # if the tag is already attached.
                        Opinion.tags.through.objects.get_or_create(
                            opinion_id=opinion_id,
                            tag_id=tag_id,
                        )
                        tags_auto_applied += 1

            scanned += len(rows)
            last_opinion_id = opinion_ids[-1]

            if scanned % (OPINION_CHUNK_SIZE * 4) == 0 or scanned >= total_in_scope:
                elapsed = time.time() - run_started
                rate = scanned / max(elapsed, 0.001)
                eta = (total_in_scope - scanned) / max(rate, 0.001)
                self.stdout.write(
                    f"  scanned {scanned:>6,}/{total_in_scope:,}  "
                    f"pending={suggestions_pending:>5,}  "
                    f"auto={suggestions_auto:>4,}  "
                    f"({rate:>4.0f}/s, eta {eta/60:.0f}min)"
                )

        elapsed = time.time() - run_started
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Done in {elapsed/60:.1f} min."))
        self.stdout.write(
            f"  opinions scanned:    {scanned:>7,}\n"
            f"  pending suggestions: {suggestions_pending:>7,}\n"
            f"  auto-applied:        {suggestions_auto:>7,}\n"
            f"  tags attached:       {tags_auto_applied:>7,}"
            + ("\n  (DRY RUN -- nothing saved)" if dry_run else "")
        )
