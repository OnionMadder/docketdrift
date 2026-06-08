"""Embed each Tag's (label + description) into Tag.embedding via voyage-law-2.

Tiny one-shot job -- 31 starter tags grow to maybe a few hundred over
time, all comfortably fit in a single Voyage API batch (their cap is
128 docs per call). No rate-limit dance needed.

Storage: ``Tag.embedding`` is a JSONField holding 1024 floats, NOT a
native MariaDB VECTOR. The cosine math during ``suggest_tags`` runs in
Python after loading all tag vectors into memory once -- with ~100
tags that's ~800KB, trivial. Pulling tag embeddings via the ORM
keeps the command portable between SQLite dev and MariaDB prod.

Idempotent on ``Tag.embedded_at``: existing-embedded tags skip unless
``--force`` is passed. Re-embed when:
- A tag's label or description was edited.
- The model is upgraded (voyage-law-3, ...).
- You suspect a corruption.

Usage::

    python manage.py embed_tags             # embed tags without embeddings
    python manage.py embed_tags --force     # re-embed everything
    python manage.py embed_tags --dry-run   # show what would be embedded

Requires VOYAGE_API_KEY in environment (typically loaded from .env).
"""
from __future__ import annotations

import json
import os
import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from opinions.models import Tag


# Same model + endpoint as embed_opinions.py so opinion-tag cosine
# comparisons in suggest_tags are meaningful (voyage-law-2 embeds
# query + document spaces compatibly).
VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-law-2"
REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 15
# Voyage's per-batch document cap. We embed all tags in one call since
# the total is far under this limit; left as a constant so anyone reading
# the code understands the upper bound.
VOYAGE_BATCH_CAP = 128


def _tag_input_text(tag: Tag) -> str:
    """Build the text we send to Voyage for one tag.

    Combining label + description gives the embedding both the canonical
    short name (Fourth Amendment) and the topical context (search and
    seizure, suppression motions, ...). Empty descriptions just send the
    label alone -- still useful, just lower signal.
    """
    label = (tag.label or "").strip()
    desc = (tag.description or "").strip()
    if desc:
        return f"{label}. {desc}"
    return label


def _voyage_embed(texts: list[str], api_key: str) -> list[list[float]]:
    """POST to Voyage; return list of embedding vectors.

    Raises ``RuntimeError`` with the API's error body on non-2xx so a
    bad API key / model name / payload surfaces immediately.
    """
    response = requests.post(
        VOYAGE_EMBED_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "input": texts,
            "model": VOYAGE_MODEL,
            "input_type": "document",
            "truncation": True,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        body = response.text[:500].replace("\n", " ")
        raise RuntimeError(
            f"Voyage API {response.status_code} {response.reason}: {body}"
        )
    payload = response.json()
    return [item["embedding"] for item in payload["data"]]


class Command(BaseCommand):
    help = "Embed each Tag's label + description into Tag.embedding via voyage-law-2."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-embed even tags that already have an embedding.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be embedded without calling Voyage.",
        )

    def handle(self, *args, force, dry_run, **options):
        qs = Tag.objects.order_by("category", "slug")
        if not force:
            qs = qs.filter(embedded_at__isnull=True)

        tags = list(qs)
        if not tags:
            self.stdout.write(self.style.SUCCESS(
                "All tags already embedded. Nothing to do (pass --force to re-embed)."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Embedding {len(tags)} tag{'' if len(tags) == 1 else 's'} via {VOYAGE_MODEL}."
        ))

        if dry_run:
            for tag in tags:
                self.stdout.write(f"  would embed: {tag.slug}  ({_tag_input_text(tag)[:80]!r})")
            self.stdout.write(self.style.WARNING("(DRY RUN -- nothing saved)"))
            return

        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise CommandError(
                "VOYAGE_API_KEY not set. Add it to your .env:\n"
                "    VOYAGE_API_KEY=pa-xxxxxxxxxxxxxxxxxxxx\n"
                "Embed-only operations are free under Voyage's tier; this won't "
                "blow your budget."
            )

        # Single batch -- tag count is always well under Voyage's 128-doc cap.
        if len(tags) > VOYAGE_BATCH_CAP:
            raise CommandError(
                f"Tag count ({len(tags)}) exceeds Voyage's per-batch cap "
                f"({VOYAGE_BATCH_CAP}). Split this into multiple runs by "
                f"filtering on --category or extend this command to chunk."
            )

        texts = [_tag_input_text(t) for t in tags]

        t0 = time.time()
        embeddings: list[list[float]] | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embeddings = _voyage_embed(texts, api_key)
                break
            except Exception as exc:
                if attempt >= MAX_RETRIES:
                    raise CommandError(
                        f"Voyage API failed {MAX_RETRIES}x: {type(exc).__name__}: {exc}"
                    )
                self.stderr.write(self.style.WARNING(
                    f"  API error (attempt {attempt}/{MAX_RETRIES}) "
                    f"{type(exc).__name__}: {exc}; retrying in {RETRY_SLEEP_SECONDS}s..."
                ))
                time.sleep(RETRY_SLEEP_SECONDS)

        assert embeddings is not None
        if len(embeddings) != len(tags):
            raise CommandError(
                f"Voyage returned {len(embeddings)} vectors but we sent {len(tags)} tags. "
                "Aborting before corrupting Tag.embedding."
            )

        # Persist. One UPDATE per tag (count is small; no need for bulk_update).
        now = timezone.now()
        for tag, vec in zip(tags, embeddings):
            tag.embedding = vec
            tag.embedded_at = now
            tag.save(update_fields=["embedding", "embedded_at"])

        elapsed = time.time() - t0
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Embedded {len(tags)} tag{'' if len(tags) == 1 else 's'} in {elapsed:.1f}s."
        ))
        for tag in tags:
            self.stdout.write(f"  [OK] {tag.slug}  ({tag.category})")
