"""Embed Opinion.raw_text into the VECTOR column via voyage-law-2.

Designed for unattended overnight runs:

- **Resumable.** Each run picks up rows where ``embedding_pending IS
  TRUE`` (indexed). Safe to interrupt with Ctrl+C, a kill, or NFSN's
  wallclock cull, then restart -- it resumes from the same point.
- **Bounded.** ``--max-runtime`` stops a run cleanly after N seconds,
  leaving the rest for the next invocation. The cron tick uses this to
  stay under NFSN's ~10-minute wallclock cull.
- **Single-flight.** An advisory ``flock`` prevents two runs (an
  overrunning cron tick + the next tick, or a manual run + a tick) from
  embedding the same rows twice.
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

On NFSN this is driven by a SCHEDULED TASK, not a resident daemon:
``scripts/embed_tick.sh`` runs one bounded pass (``--max-runtime 480``)
every ~10 minutes, exits cleanly under NFSN's wallclock cull, and the
next tick resumes. No self-respawning wrapper, no sentinel files -- the
NFSN scheduler is the supervisor and emails on any non-zero exit. The
heartbeat only watches the ``.embed_progress`` beacon for a stall. See
``scripts/embed_tick.sh`` for the runbook.

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
# Voyage's embed endpoint accepts up to 128 documents per call (their max),
# but ALSO caps total tokens per batch at 120K. Legal opinions average
# ~5-10K tokens each, so 128-doc batches blow that token cap immediately.
# We use --batch-size as a HARD CAP on rows fetched per iteration and let
# --max-batch-tokens dynamically pack the batch under Voyage's limit.
DEFAULT_BATCH = 256
# Voyage's hard limit is 120,000 tokens per request. Our estimate
# (~4 chars/token via _estimate_tokens) is approximate -- a batch
# packed under our 100K cap could still overshoot Voyage's 120K cap
# once Voyage's own tokenizer counts. AZ has a small handful of
# death-penalty appeals over 100K chars each that crossed the line
# in batches that looked safe to us. Drop to 90K so even an "oops,
# our estimate was off by 2x" outcome stays under Voyage's ceiling.
DEFAULT_MAX_BATCH_TOKENS = 90_000
# voyage-law-2's context window. Anything longer gets truncation=true'd
# away, so we cap our estimate here too.
MODEL_CONTEXT_TOKENS = 16_000
# Heuristic: legal English averages ~4 chars per token (between code-3
# and prose-4.5). Generous enough to not under-estimate badly.
EST_CHARS_PER_TOKEN = 4
# Voyage free tier: 60 requests/minute. Paid tier: 600+. Adjust via --rpm.
DEFAULT_RPM = 60
# Voyage-law-2 list price per 1M tokens. Used only to estimate cumulative
# cost in the progress log so you can spot surprises.
PRICE_PER_M_TOKENS_USD = 0.12
# Auto-retry on transient API errors -- some flakes are normal across a
# long batch run.
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 30
# DB retry config -- NFSN's MariaDB sits behind an SSL connection that
# drops every few hours mid-query. Without retry, the embed loop dies
# with KeyboardInterrupt mid-UPDATE and the whole run aborts. We catch
# anything during the persist step, close the broken connection so
# Django re-establishes fresh, and retry.
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP_SECONDS = 5

# Cron-tick model. embed_opinions is driven by an NFSN scheduled task
# (scripts/embed_tick.sh) every ~10 min, not a self-respawning daemon.
# 0 = run until the corpus is fully embedded (the default; right for a
# manual full run). The cron tick passes --max-runtime 480 so each pass
# exits cleanly well under NFSN's ~10-minute wallclock cull and the next
# tick resumes via the indexed embedding_pending flag.
DEFAULT_MAX_RUNTIME = 0
# Single-flight advisory lock so an overrunning tick never overlaps the
# next one (and a manual run never collides with a tick). flock is
# released automatically when the process dies -- no stale-lock cleanup.
LOCK_FILENAME = ".embed.lock"
# Progress beacon the heartbeat reads to confirm the pipeline is alive and
# advancing: a single line "<unix_ts> <pending_remaining>". Rewritten
# every batch and at clean exit. A stale beacon with pending > 0 means
# ticks aren't running or aren't finishing -> heartbeat alerts.
PROGRESS_FILENAME = ".embed_progress"

VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"
# Per-request HTTP timeout. Embed inference on a token-packed batch of
# legal text can take 10-30s; pad generously.
REQUEST_TIMEOUT_SECONDS = 180


def _estimate_tokens(text: str) -> int:
    """Rough token count for a single document, capped at the model context."""
    return min(len(text) // EST_CHARS_PER_TOKEN, MODEL_CONTEXT_TOKENS)


def _voyage_embed(texts: list[str], model: str, api_key: str) -> tuple[list[list[float]], int]:
    """POST to Voyage's embeddings endpoint; return (embeddings, total_tokens).

    Surfaces the API's error response body on non-2xx (Voyage 400s carry a
    JSON ``detail`` field that pinpoints the bad parameter -- without it
    the retry loop just sees "400 Bad Request" with no clue what's wrong).
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
    if not response.ok:
        # Truncate the body in case it's huge; first 500 chars is plenty
        # to spot the wrong-model-name / token-limit / etc reason.
        body = response.text[:500].replace("\n", " ")
        raise RuntimeError(
            f"Voyage API {response.status_code} {response.reason}: {body}"
        )
    payload = response.json()
    embeddings = [item["embedding"] for item in payload["data"]]
    tokens = payload.get("usage", {}).get("total_tokens", 0)
    return embeddings, tokens


def _runtime_dir() -> str:
    """Directory for the lock + progress beacon files (the repo root)."""
    from django.conf import settings
    return str(settings.BASE_DIR)


def _disable_statement_timeout() -> None:
    """Lift this connection's per-statement timeout.

    settings.py sets ``max_statement_time = 25`` via ``init_command`` on
    every new MariaDB connection to protect gunicorn workers. The embed
    batch queries are background work that can legitimately run longer, so
    we lift the cap. MUST be re-called after any reconnect: a fresh
    connection re-runs init_command and silently restores the 25s cap.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION max_statement_time = 0")


def _acquire_singleflight_lock():
    """Take an advisory single-flight lock; return the held fd or None.

    Uses ``fcntl.flock`` (POSIX only). This is reached only after the
    MariaDB-vendor check, so local Windows/SQLite never gets here. Returns
    the open file object on success -- the CALLER must keep a reference for
    the process lifetime to hold the lock (it releases when the fd closes /
    the process dies). Returns None if another run already holds it.
    """
    import fcntl

    path = os.path.join(_runtime_dir(), LOCK_FILENAME)
    fh = open(path, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        fh.close()
        return None
    return fh


def _write_progress(remaining: int) -> None:
    """Rewrite the heartbeat's progress beacon. Best-effort, never raises."""
    try:
        path = os.path.join(_runtime_dir(), PROGRESS_FILENAME)
        with open(path, "w") as fh:
            fh.write("%d %d\n" % (int(time.time()), max(0, remaining)))
    except BaseException:
        pass


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
            help=(
                f"Hard cap on rows fetched per iteration (default {DEFAULT_BATCH}, "
                "Voyage's API max). The actual batch is dynamically packed under "
                "--max-batch-tokens, so this just bounds the inner loop."
            ),
        )
        parser.add_argument(
            "--max-batch-tokens",
            type=int,
            default=DEFAULT_MAX_BATCH_TOKENS,
            help=(
                f"Per-request token cap (default {DEFAULT_MAX_BATCH_TOKENS:,}). "
                "Voyage rejects batches over 120K tokens; we pack each batch "
                "under this estimate to stay safe."
            ),
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
        parser.add_argument(
            "--state",
            default=None,
            help=(
                "USPS 2-letter state code (e.g. NH). Restrict embedding "
                "to opinions in that state's courts only. Useful when "
                "running unattended and you want to finish one state's "
                "corpus before tackling the next."
            ),
        )
        parser.add_argument(
            "--max-runtime",
            type=int,
            default=DEFAULT_MAX_RUNTIME,
            help=(
                "Stop cleanly after roughly this many seconds, leaving any "
                "remaining work for the next run (it resumes via the indexed "
                "embedding_pending flag). 0 = run until the corpus is done "
                f"(default {DEFAULT_MAX_RUNTIME}; use for manual full runs). "
                "The cron tick (scripts/embed_tick.sh) passes 480 to stay "
                "under NFSN's ~10-minute wallclock cull."
            ),
        )

    def handle(self, *args, limit, batch_size, max_batch_tokens, rpm, model,
               state, max_runtime, **options):
        if connection.vendor != "mysql":
            raise CommandError(
                f"Embedding requires MariaDB / MySQL (got {connection.vendor!r}). "
                "Local SQLite dev doesn't have a VECTOR column."
            )

        # Single-flight: bail cleanly if another run already holds the
        # lock. Exit 0 -- this is the EXPECTED outcome under the every-10-min
        # cron cadence whenever a previous tick is still running. `lock_fh`
        # MUST stay referenced for the whole run so the advisory lock is
        # held until the process exits (do not close it).
        lock_fh = _acquire_singleflight_lock()  # noqa: F841 (held for lifetime)
        if lock_fh is None:
            self.stdout.write(
                "Another embed run holds the lock; skipping this tick."
            )
            return

        # Lift the per-statement timeout set by settings' init_command (the
        # 25s cap protects gunicorn workers but is too tight for the startup
        # COUNT on a low-coverage state). Re-applied after any reconnect --
        # see the DB-retry block below.
        _disable_statement_timeout()

        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise CommandError(
                "VOYAGE_API_KEY not set. Add it to your .env:\n"
                "    VOYAGE_API_KEY=pa-xxxxxxxxxxxxxxxxxxxx\n"
                "Get a key at https://www.voyageai.com/  "
                "(free tier covers the initial 60K-opinion run)."
            )

        # Optional state filter: restrict to one state's court_ids.
        # Done as a raw IN-list (court ids are small ints, no SQL-injection
        # risk) appended to every embedding-loop query.
        state_clause = ""
        state_params: list = []
        if state:
            from opinions.models import Court, State
            try:
                state_obj = State.objects.get(code=state.upper())
            except State.DoesNotExist:
                raise CommandError(f"State {state.upper()!r} not found.")
            court_ids = list(state_obj.courts.values_list("id", flat=True))
            if not court_ids:
                raise CommandError(f"No courts found for state {state.upper()!r}.")
            placeholders = ",".join(["%s"] * len(court_ids))
            state_clause = f" AND court_id IN ({placeholders})"
            state_params = court_ids
            self.stdout.write(
                f"  [state filter] restricting to {state.upper()} "
                f"({len(court_ids)} court(s))"
            )

        # Count work remaining.
        # Migration 0023 added the indexed `embedding_pending` column so
        # we can stop using `WHERE embedding IS NULL` (which can't use
        # an index because the embedding VECTOR column isn't indexable
        # for NULL-ness). The composite index (embedding_pending,
        # court_id) makes this sub-100ms regardless of corpus size.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM opinions_opinion "
                "WHERE embedding_pending = TRUE AND raw_text != ''" + state_clause,
                state_params,
            )
            total_to_do = cursor.fetchone()[0]

        if total_to_do == 0:
            # Refresh the beacon to pending=0 so the heartbeat treats the
            # state as complete and never alerts on a stale beacon.
            _write_progress(0)
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
            f"  fetch_cap={batch_size}  max_tokens/batch={max_batch_tokens:,}  "
            f"target_rpm={rpm}  price=${PRICE_PER_M_TOKENS_USD:.2f}/M tokens\n"
        )

        seconds_between_batches = 60.0 / rpm
        last_call_ts = 0.0
        embedded_total = 0
        tokens_total = 0
        run_started = time.time()
        # max_runtime=0 means "no budget" (manual full run); the cron tick
        # passes a budget so it exits under NFSN's wallclock cull.
        deadline = run_started + max_runtime if max_runtime else None

        while embedded_total < total_to_do:
            # Stop cleanly when the time budget is spent; the next run
            # resumes from the same point via embedding_pending.
            if deadline is not None and time.time() >= deadline:
                self.stdout.write(
                    f"Reached --max-runtime ({max_runtime}s) after "
                    f"{embedded_total:,} this run; stopping cleanly. "
                    "Remaining work resumes on the next run."
                )
                break

            # Batch fetch uses embedding_pending (indexed) instead of
            # embedding IS NULL (full table scan). See migration 0023.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, raw_text FROM opinions_opinion "
                    "WHERE embedding_pending = TRUE AND raw_text != ''" + state_clause + " "
                    "ORDER BY id "
                    "LIMIT %s",
                    state_params + [batch_size],
                )
                fetched = cursor.fetchall()

            if not fetched:
                break  # All done

            # Pack as many rows as we can under Voyage's per-request token
            # cap. Single huge docs that alone exceed the budget still go
            # solo (truncation=true clamps them to MODEL_CONTEXT_TOKENS,
            # which fits even MODEL_CONTEXT < max_batch_tokens by design).
            batch = []
            batch_estimated_tokens = 0
            for row in fetched:
                et = _estimate_tokens(row[1])
                if batch and batch_estimated_tokens + et > max_batch_tokens:
                    break
                batch.append(row)
                batch_estimated_tokens += et
            if not batch:  # safety net -- shouldn't happen but be defensive
                batch = [fetched[0]]
                batch_estimated_tokens = _estimate_tokens(fetched[0][1])
            rows = batch  # name the inner loop expects

            # Rate limit -- wait between batches if we'd exceed RPM
            elapsed = time.time() - last_call_ts
            if elapsed < seconds_between_batches:
                time.sleep(seconds_between_batches - elapsed)

            texts = [r[1] for r in rows]

            # Embed with retry. Transient 5xx / 429 / network blips warrant
            # a short cooldown. We catch BaseException (not just Exception)
            # so SSL-read interruptions -- which Python's socket layer raises
            # as KeyboardInterrupt when the SSL socket gets EINTR'd by a
            # signal -- become retryable instead of fatal. (NFSN sends those
            # interrupts every few hours of a long background process.)
            # Real SIGTERM/SIGKILL still take the process down.
            embeddings, batch_tokens = None, 0
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    embeddings, batch_tokens = _voyage_embed(texts, model, api_key)
                    break
                except BaseException as exc:
                    if attempt >= MAX_RETRIES:
                        # Raise CommandError instead of `return` so the
                        # process exits non-zero. The supervisor wrapper
                        # reads $? to decide whether to resurrect or stand
                        # down -- a `return` here looked like "success" to
                        # the shell and made the AZ wrapper falsely report
                        # "all done" after the first Voyage 400 hit.
                        raise CommandError(
                            f"\nAPI failed {MAX_RETRIES}x for this batch -- exiting. "
                            f"Re-run the command to resume from the same point.\n"
                            f"Last error: {type(exc).__name__}: {exc}"
                        )
                    self.stderr.write(self.style.WARNING(
                        f"  API error (attempt {attempt}/{MAX_RETRIES}) "
                        f"{type(exc).__name__}: {exc}; sleeping {RETRY_SLEEP_SECONDS}s..."
                    ))
                    time.sleep(RETRY_SLEEP_SECONDS)

            last_call_ts = time.time()
            tokens_total += batch_tokens

            # Persist back to the VECTOR column. MariaDB's Vec_FromText()
            # takes a JSON array literal and packs it into the binary
            # vector format. We update per-row -- a tighter batch UPDATE
            # via CASE expressions could shave roundtrips, but plain
            # UPDATEs per batch are plenty fast for an overnight job.
            #
            # Retry on SSL/connection drops: NFSN's MariaDB is reached via
            # an SSL connection that occasionally cuts mid-query. Without
            # this guard the process would die with KeyboardInterrupt and
            # the whole run aborts. We close the broken connection (Django
            # auto-reconnects on next use) and retry the whole batch.
            for db_attempt in range(1, DB_MAX_RETRIES + 1):
                try:
                    with connection.cursor() as cursor:
                        for (opinion_id, _), vec in zip(rows, embeddings):
                            # Set both the VECTOR column AND the
                            # indexed embedding_pending flag in one
                            # write. The next batch fetch's index scan
                            # immediately skips these rows.
                            cursor.execute(
                                "UPDATE opinions_opinion "
                                "SET embedding = Vec_FromText(%s), "
                                "    embedding_pending = FALSE "
                                "WHERE id = %s",
                                [json.dumps(vec), opinion_id],
                            )
                    break
                except BaseException as db_exc:
                    # BaseException not Exception -- see _voyage_embed retry
                    # comment above for why; same NFSN SSL-interrupt logic.
                    if db_attempt >= DB_MAX_RETRIES:
                        # Raise (non-zero exit), do NOT `return` (exit 0).
                        # A bare return looked like "all done" to the shell
                        # and made the supervisor tear down -- the same
                        # silent-success bug fixed on the API path above.
                        # A non-zero scheduled-task exit makes NFSN email.
                        raise CommandError(
                            f"\nDB failed {DB_MAX_RETRIES}x for this batch -- exiting. "
                            f"Re-run to resume from the same point.\n"
                            f"Last error: {type(db_exc).__name__}: {db_exc}"
                        )
                    self.stderr.write(self.style.WARNING(
                        f"  DB error (attempt {db_attempt}/{DB_MAX_RETRIES}) "
                        f"{type(db_exc).__name__}: {db_exc}; "
                        f"reconnecting in {DB_RETRY_SLEEP_SECONDS}s..."
                    ))
                    try:
                        connection.close()  # Django reconnects on next use
                    except BaseException:
                        pass
                    time.sleep(DB_RETRY_SLEEP_SECONDS)
                    # The fresh connection re-ran init_command, restoring the
                    # 25s cap; re-lift it so the rest of the run keeps the
                    # intended unlimited statement time.
                    try:
                        _disable_statement_timeout()
                    except BaseException:
                        pass

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
            # Refresh the beacon every batch so the heartbeat sees forward
            # progress even if this run is later killed mid-flight.
            _write_progress(total_to_do - embedded_total)

        # Accurate end-of-run beacon: re-count true remaining so the
        # heartbeat sees 0 when the corpus is fully embedded (rather than a
        # stale positive from the per-batch estimate).
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM opinions_opinion "
                "WHERE embedding_pending = TRUE AND raw_text != ''" + state_clause,
                state_params,
            )
            _write_progress(cursor.fetchone()[0])

        elapsed_total = time.time() - run_started
        cost = tokens_total / 1_000_000 * PRICE_PER_M_TOKENS_USD
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done in {elapsed_total/60:.1f} min. "
            f"Embedded {embedded_total:,} opinions, "
            f"{tokens_total:,} tokens, ~${cost:.2f}."
        ))
