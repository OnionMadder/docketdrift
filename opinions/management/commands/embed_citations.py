"""Embed each internal citation's ``context_quote`` via voyage-law-2.

This is the second step of the "How this document has been cited" pipeline:

    extract_citations  ->  embed_citations  ->  cluster_citations

The embedding it writes (``OpinionCitation.context_embedding``, a JSON array)
is used for ONE thing only: ``cluster_citations`` groups near-identical citing
passages so the public panel can collapse them into a Scholar-style
"... and N similar citations" row. It is never read at request time.

Only INTERNAL edges (``cited_opinion`` set -- the cited case is in our corpus)
with a non-empty ``context_quote`` matter, because the panel only renders on a
cited opinion's own page. External edges and empty quotes are skipped.

Mirrors ``embed_opinions`` (Voyage HTTP via ``requests``, not the SDK -- see
that command's header for the FreeBSD/rustc reason), but the quotes are short
(~1-2 sentences) so the token packing is trivial: a fixed batch size is plenty.

- **Resumable / idempotent.** Picks up rows where ``context_embedding IS NULL``,
  so a kill mid-run (or NFSN's wallclock cull) resumes cleanly. ``--max-runtime``
  stops cleanly under the cull.
- **Robust.** API retry with backoff; DB persist retries-with-reconnect on
  NFSN's SSL drops (BaseException, same as embed_opinions).

Pipeline note: ``extract_citations`` REBUILDS each citing opinion's edges
(delete + recreate), which clears their embeddings -- so re-run this (and
``cluster_citations``) after any ``extract_citations`` pass.

Requires VOYAGE_API_KEY in the environment (same key as embed_opinions).

Usage::

    python manage.py embed_citations --state NH
    python manage.py embed_citations --state NH --limit 50   # smoke test
"""
from __future__ import annotations

import os
import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

DEFAULT_MODEL = "voyage-law-2"
# Quotes are ~1-2 sentences (<=500 chars), so well under Voyage's 120K-token
# per-request cap even at the API's 128-input max. No dynamic token packing
# needed -- a fixed batch is safe.
DEFAULT_BATCH = 128
DEFAULT_RPM = 60
PRICE_PER_M_TOKENS_USD = 0.12

MAX_RETRIES = 4
RETRY_SLEEP_SECONDS = 20
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP_SECONDS = 5

VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"
REQUEST_TIMEOUT_SECONDS = 120


def _voyage_embed(texts, model, api_key):
    """POST quotes to Voyage; return list of embeddings + total tokens."""
    response = requests.post(
        VOYAGE_EMBED_URL,
        headers={
            "Authorization": "Bearer %s" % api_key,
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
    if not response.ok:
        body = response.text[:500].replace("\n", " ")
        raise RuntimeError(
            "Voyage API %s %s: %s" % (response.status_code, response.reason, body)
        )
    payload = response.json()
    embeddings = [item["embedding"] for item in payload["data"]]
    tokens = payload.get("usage", {}).get("total_tokens", 0)
    return embeddings, tokens


class Command(BaseCommand):
    help = "Embed citation context_quotes (voyage-law-2) for clustering."

    def add_arguments(self, parser):
        parser.add_argument("--state", default=None,
                            help="USPS 2-letter code (e.g. NH). Default: all live states.")
        parser.add_argument("--limit", type=int, default=None,
                            help="Embed at most N edges (smoke test).")
        parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH,
                            help="Quotes per Voyage request (default %d)." % DEFAULT_BATCH)
        parser.add_argument("--rpm", type=int, default=DEFAULT_RPM,
                            help="Target requests/minute (default %d)." % DEFAULT_RPM)
        parser.add_argument("--model", default=DEFAULT_MODEL,
                            help="Voyage model (default %s)." % DEFAULT_MODEL)
        parser.add_argument("--max-runtime", type=int, default=0,
                            help="Stop cleanly after ~N seconds (0 = run to completion).")

    def handle(self, *args, state, limit, batch_size, rpm, model, max_runtime,
               **options):
        from opinions.models import Court, OpinionCitation, State

        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise CommandError(
                "VOYAGE_API_KEY not set. Add it to your .env (same key as "
                "embed_opinions):\n    VOYAGE_API_KEY=pa-xxxxxxxx"
            )

        if state:
            codes = [state.upper()]
        else:
            codes = list(State.objects.filter(is_live=True).values_list("code", flat=True))

        for code in codes:
            court_ids = list(
                Court.objects.filter(state__code=code).values_list("id", flat=True)
            )
            if not court_ids:
                continue
            # Only internal edges with a real quote, not yet embedded. The
            # cited case must be in this state's corpus (its page is where the
            # panel renders).
            base = (
                OpinionCitation.objects
                .filter(cited_opinion__court_id__in=court_ids)
                .filter(context_embedding__isnull=True)
                .exclude(context_quote="")
            )
            ids = list(base.order_by("id").values_list("id", flat=True))
            if limit:
                ids = ids[:limit]
            if not ids:
                self.stdout.write("%s: nothing to embed." % code)
                continue

            self.stdout.write(self.style.SUCCESS(
                "%s: embedding %d citation quotes via %s." % (code, len(ids), model)
            ))

            seconds_between = 60.0 / rpm if rpm > 0 else 0.0
            last_call = 0.0
            done = tokens_total = 0
            run_started = time.time()
            deadline = run_started + max_runtime if max_runtime else None

            for start in range(0, len(ids), batch_size):
                if deadline is not None and time.time() >= deadline:
                    self.stdout.write(
                        "  reached --max-runtime; stopping after %d (resume to "
                        "finish)." % done
                    )
                    break
                chunk_ids = ids[start:start + batch_size]
                rows = list(
                    OpinionCitation.objects.filter(id__in=chunk_ids)
                    .values_list("id", "context_quote")
                )
                if not rows:
                    continue

                elapsed = time.time() - last_call
                if elapsed < seconds_between:
                    time.sleep(seconds_between - elapsed)

                texts = [r[1] for r in rows]
                embeddings = None
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        embeddings, batch_tokens = _voyage_embed(texts, model, api_key)
                        break
                    except BaseException as exc:
                        if attempt >= MAX_RETRIES:
                            raise CommandError(
                                "API failed %dx for a batch -- exiting; re-run to "
                                "resume. Last error: %s: %s"
                                % (MAX_RETRIES, type(exc).__name__, exc)
                            )
                        self.stderr.write(self.style.WARNING(
                            "  API error (attempt %d/%d) %s: %s; sleeping %ds..."
                            % (attempt, MAX_RETRIES, type(exc).__name__, exc,
                               RETRY_SLEEP_SECONDS)
                        ))
                        time.sleep(RETRY_SLEEP_SECONDS)
                last_call = time.time()
                tokens_total += batch_tokens

                # Persist each embedding (JSONField; works on MariaDB + SQLite).
                # Retry-with-reconnect on NFSN's SSL drops.
                for db_attempt in range(1, DB_MAX_RETRIES + 1):
                    try:
                        for (cid, _), vec in zip(rows, embeddings):
                            OpinionCitation.objects.filter(id=cid).update(
                                context_embedding=vec
                            )
                        break
                    except BaseException as db_exc:
                        if db_attempt >= DB_MAX_RETRIES:
                            raise CommandError(
                                "DB failed %dx for a batch -- exiting; re-run to "
                                "resume. Last error: %s: %s"
                                % (DB_MAX_RETRIES, type(db_exc).__name__, db_exc)
                            )
                        self.stderr.write(self.style.WARNING(
                            "  DB error (attempt %d/%d) %s; reconnecting..."
                            % (db_attempt, DB_MAX_RETRIES, type(db_exc).__name__)
                        ))
                        try:
                            connection.close()
                        except BaseException:
                            pass
                        time.sleep(DB_RETRY_SLEEP_SECONDS)

                done += len(rows)
                cost = tokens_total / 1e6 * PRICE_PER_M_TOKENS_USD
                self.stdout.write(
                    "  [%d/%d] tokens=%s cost=$%.3f"
                    % (done, len(ids), format(tokens_total, ","), cost)
                )

            cost = tokens_total / 1e6 * PRICE_PER_M_TOKENS_USD
            self.stdout.write(self.style.SUCCESS(
                "%s done. embedded=%d tokens=%s ~$%.3f"
                % (code, done, format(tokens_total, ","), cost)
            ))
