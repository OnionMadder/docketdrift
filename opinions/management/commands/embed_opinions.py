"""Embed Opinion.raw_text into the VECTOR column via voyage-law-2.

Designed for unattended overnight runs:

- **Resumable.** Each run picks up rows where ``embedding IS NULL``. Safe
  to interrupt with Ctrl+C or restart after a crash; just re-run.
- **Rate-limited.** Default 60 batches/min matches Voyage's free tier;
  raise via ``--rpm`` if you're on paid (600 RPM).
- **Truncating.** ``truncation=True`` lets Voyage auto-truncate any
  opinion longer than the model's 16K-token context window. No code-side
  chunking needed for v1; if we later want per-paragraph search, we'd
  shift to chunk-then-average.
- **Cost-aware.** Logs cumulative tokens + estimated dollars every batch
  so you can spot runaway-cost surprises early.

We hit Voyage's HTTP API directly with ``requests`` (already installed)
rather than the ``voyageai`` Python SDK -- the SDK transitively depends
on ``orjson>=3.11`` which requires ``rustc>=1.95``, but NFSN's FreeBSD
host has rustc 1.89. The API is dead simple (OpenAI-compatible shape)
so dropping the SDK is a tiny no-op.

Usage::

    # The full overnight job:
    .venv/bin/python manage.py embed_opinions

    # Smoke test against 500 opinions first:
    .venv/bin/python manage.py embed_opinions --limit 500

    # Higher throughput if on Voyage paid tier:
    .venv/bin/python manage.py embed_opinions --rpm 600

Background-friendly invocation on NFSN (close SSH, come back tomorrow)::

    nohup .venv/bin/python manage.py embed_opinions \\
        > /home/private/docketdrift/embed.log 2>&1 &

Requires VOYAGE_API_KEY in environment (typically in .env). Get one at
voyageai.com -- free tier is enough for the initial 60K corpus run.
"""
from __future__ import annotations

import json
import os
import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

# Voyage's "voyage-law-2" model -- 1024-dim, legal-domain-tuned, 16K context
DEFAULT_MODEL = "voyage-law-2"
# Voyage's embed endpoint accepts up to 128 documents per call (their max).
# Bigger batch = fewer roundtrips, but each call's wall-clock grows with
# embed inference time.
DEFAULT_BATCH = 128
# Voyage free tier: 60 requests/minute. Paid tier: 600+. Adjust via --rpm.
DEFAULT_RPM = 60
# Voyage-law-2 list price per 1M tokens. Used only to estimate cumulative
# cost in the progress log so you can spot surprises.
PRICE_PER_M_TOKENS_USD = 0.12
# Auto-retry on transient API errors -- some flakes are normal across a
# long batch run.
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 30

VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"
# Per-request HTTP timeout. Embed inference on a 128-doc batch of legal
# text can take 10-30s; pad generously so we don't error out on cold-
# cache slowness.
REQUEST_TIMEOUT_SECONDS = 180


def _voyage_embed(texts: list[str], model: str, api_key: str) -> tuple[list[list[float]], int]:
    """POST to Voyage's embeddings endpoint; return (embeddings, total_tokens).

    Raises ``requests.HTTPError`` on non-2xx so the caller can retry.
    """
    response = requests.post(
        VOYAGE_EMBED_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "input": texts,
            "model": model,
            "input_type": "document",
            "truncation": True,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    embeddings = [item["embedding"] for item in payload["data"]]
    tokens = payload.get("usage", {}).get("total_tokens", 0)
    return embeddings, tokens


class Command(BaseCommand):
    help = "Embed Opinion.raw_text into VECTOR column via voyage-law-2."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N opinions (smoke-test convenience).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH,
            help=f"Documents per embed call (default {DEFAULT_BATCH}, Voyage max 128).",
        )
        parser.add_argument(
            "--rpm",
            type=int,
            default=DEFAULT_RPM,
            help=f"Target requests/minute (default {DEFAULT_RPM} for Voyage free tier).",
        )
        parser.add_argument(
            "--model",
            default=DEFAULT_MODEL,
            help=f"Voyage model name (default {DEFAULT_MODEL}).",
        )

    def handle(self, *args, limit, batch_size, rpm, model, **options):
        if connection.vendor != "mysql":
            raise CommandError(
                f"Embedding requires MariaDB / MySQL (got {connection.vendor!r}). "
                "Local SQLite dev doesn't have a VECTOR column."
            )

        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise CommandError(
                "VOYAGE_API_KEY not set. Add it to your .env:\n"
                "    VOYAGE_API_KEY=pa-xxxxxxxxxxxxxxxxxxxx\n"
                "Get a key at https://www.voyageai.com/  "
                "(free tier covers the initial 60K-opinion run)."
            )

        # Count work remaining
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM opinions_opinion "
                "WHERE embedding IS NULL AND raw_text != ''"
            )
            total_to_do = cursor.fetchone()[0]

        if total_to_do == 0:
            self.stdout.write(self.style.SUCCESS(
                "All opinions already embedded. Nothing to do."
            ))
            return

        if limit:
            total_to_do = min(total_to_do, limit)

        self.stdout.write(self.style.SUCCESS(
            f"Embedding {total_to_do:,} opinions via {model}."
        ))
        self.stdout.write(
            f"  batch={batch_size}  target_rpm={rpm}  "
            f"price=${PRICE_PER_M_TOKENS_USD:.2f}/M tokens\n"
        )

        seconds_between_batches = 60.0 / rpm
        last_call_ts = 0.0
        embedded_total = 0
        tokens_total = 0
        run_started = time.time()

        while embedded_total < total_to_do:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, raw_text FROM opinions_opinion "
                    "WHERE embedding IS NULL AND raw_text != '' "
                    "LIMIT %s",
                    [batch_size],
                )
                rows = cursor.fetchall()

            if not rows:
                break  # All done

            # Rate limit -- wait between batches if we'd exceed RPM
            elapsed = time.time() - last_call_ts
            if elapsed < seconds_between_batches:
                time.sleep(seconds_between_batches - elapsed)

            texts = [r[1] for r in rows]

            # Embed with retry. Transient 5xx / 429 / network blips warrant
            # a short cooldown.
            embeddings, batch_tokens = None, 0
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    embeddings, batch_tokens = _voyage_embed(texts, model, api_key)
                    break
                except Exception as exc:
                    if attempt >= MAX_RETRIES:
                        self.stderr.write(self.style.ERROR(
                            f"\nAPI failed {MAX_RETRIES}x for this batch -- exiting. "
                            f"Re-run the command to resume from the same point.\n"
                            f"Last error: {exc}"
                        ))
                        return
                    self.stderr.write(self.style.WARNING(
                        f"  API error (attempt {attempt}/{MAX_RETRIES}): {exc}; "
                        f"sleeping {RETRY_SLEEP_SECONDS}s..."
                    ))
                    time.sleep(RETRY_SLEEP_SECONDS)

            last_call_ts = time.time()
            tokens_total += batch_tokens

            # Persist back to the VECTOR column. MariaDB's Vec_FromText()
            # takes a JSON array literal and packs it into the binary
            # vector format. We update per-row -- a tighter batch UPDATE
            # via CASE expressions could shave roundtrips, but 128 simple
            # UPDATEs per batch is plenty fast for an overnight job.
            with connection.cursor() as cursor:
                for (opinion_id, _), vec in zip(rows, embeddings):
                    cursor.execute(
                        "UPDATE opinions_opinion "
                        "SET embedding = Vec_FromText(%s) "
                        "WHERE id = %s",
                        [json.dumps(vec), opinion_id],
                    )

            embedded_total += len(rows)
            elapsed_total = time.time() - run_started
            rate = embedded_total / max(elapsed_total, 0.001)
            eta_sec = (total_to_do - embedded_total) / max(rate, 0.001)
            cost_so_far = tokens_total / 1_000_000 * PRICE_PER_M_TOKENS_USD

            self.stdout.write(
                f"  [{embedded_total:>6,}/{total_to_do:,}] "
                f"rate={rate:>5.1f}/s  "
                f"tokens={tokens_total:>11,}  "
                f"cost=${cost_so_far:>5.2f}  "
                f"eta={eta_sec/60:>4.0f}min",
                ending="\n",
            )

        elapsed_total = time.time() - run_started
        cost = tokens_total / 1_000_000 * PRICE_PER_M_TOKENS_USD
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done in {elapsed_total/60:.1f} min. "
            f"Embedded {embedded_total:,} opinions, "
            f"{tokens_total:,} tokens, ~${cost:.2f}."
        ))
