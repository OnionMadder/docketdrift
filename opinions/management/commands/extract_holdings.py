"""Extract a plain-language "summarized holding" for each opinion via Claude Haiku.

This is the ONE place DocketDrift lets an LLM produce prose. Everything else
on the site is deterministic regex/parser extraction or embedding-similarity
ranking (no text generation) -- see ``/how-we-differ/``. The summarized
holding is a deliberate, narrowly-scoped exception with strong framing
guardrails:

- The model reads ONLY the opinion's own text (truncated to the first
  ~6,000 tokens, where appellate holdings nearly always sit) and is told to
  use the court's own language, add no interpretation, and stay bounded to
  what the opinion says.
- Each summary is stored with its OWN ``holding_review_status`` (default
  ``AI_ONLY``), parallel to ``Opinion.review_status``, so the public page
  shows an amber dot ("Summarized by Claude Haiku, not yet human-reviewed")
  until an editor reads it and flips it to REVIEWED (cyan).
- The public section is headed "Summarized holding", not "AI-generated" --
  more honest about what it is and it dodges the AI-as-scarlet-letter framing.

**NH-only for now** (Onion's proving-ground rule): build + verify every new
feature on NH first, then roll out to MN/AZ.

Design mirrors ``embed_opinions``:

- **Resumable / idempotent.** Each run picks up rows where
  ``holding_summary = ''`` (and not human-REVIEWED), so a kill mid-batch
  resumes cleanly -- every opinion is persisted the moment its summary comes
  back, via a targeted ``.update()`` (NOT ``Opinion.save()``, which would
  re-run the parser save-hook).
- **Direct HTTP, no SDK.** We hit the Anthropic Messages API with
  ``requests`` rather than the ``anthropic`` Python SDK -- exactly the reason
  ``embed_opinions`` avoids the ``voyageai`` SDK: the SDK pulls in
  Rust-extension wheels (``pydantic-core``, ``jiter``) that NFSN's FreeBSD
  host + rustc 1.89 can't build. The Messages API is a simple JSON POST, so
  dropping the SDK is a tiny no-op and avoids a new, FreeBSD-risky dependency.
- **Cost-aware.** Logs cumulative input/output tokens + estimated dollars so
  you can spot a runaway bill early. Haiku 4.5 is $1/$5 per 1M in/out tokens.
- **Robust.** Per-opinion try/except -- one bad opinion logs and the batch
  continues. Exponential backoff on 429/5xx/network; honors ``retry-after``.
  DB persist retries-with-reconnect on NFSN's SSL connection drops.

Requires ANTHROPIC_API_KEY in the environment (typically NFSN's .env).

Usage::

    # Sanity-check the prompt on 10 random NH opinions WITHOUT committing:
    python manage.py extract_holdings --state NH --smoke

    # Small test batch (writes):
    python manage.py extract_holdings --state NH --limit 25

    # The full NH corpus (the long-running step; resumable):
    python manage.py extract_holdings --state NH

    # Re-extract after tuning the prompt (overwrites existing summaries but
    # never a human-REVIEWED one):
    python manage.py extract_holdings --state NH --force
"""
from __future__ import annotations

import json
import os
import re
import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

# Latest Haiku -- cheapest model that's plenty for bounded extraction. The
# alias resolves to the current dated snapshot server-side. Printed in the
# run header and stored on each row's holding_model so a later re-run with a
# better model is auditable.
DEFAULT_MODEL = "claude-haiku-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Haiku 4.5 list price per 1M tokens (in / out). Used only to estimate
# cumulative cost in the progress log.
PRICE_IN_PER_M_USD = 1.0
PRICE_OUT_PER_M_USD = 5.0

# Holdings live in the first third of an opinion; truncating the input keeps
# cost and latency down with no quality loss. ~4 chars/token (the same
# heuristic embed_opinions uses) -> ~6,000 input tokens.
DEFAULT_MAX_INPUT_CHARS = 24_000
# The summary is 2-3 sentences + a short JSON array; 400 output tokens is
# generous headroom.
MAX_OUTPUT_TOKENS = 400

# Polite default pacing. The real limiter is the API's own 429 + retry-after,
# which we honor; raise --rpm if your org tier allows more throughput.
DEFAULT_RPM = 50
# Per-request HTTP timeout. Haiku on a 6K-token input is fast, but pad.
REQUEST_TIMEOUT_SECONDS = 120

# API retry (transient 429 / 5xx / network blips).
MAX_RETRIES = 5
RETRY_BASE_SLEEP_SECONDS = 5
RETRY_MAX_SLEEP_SECONDS = 60

# DB retry config -- NFSN's MariaDB sits behind an SSL connection that drops
# every few hours mid-query. Same defense as embed_opinions: catch anything
# during the persist, close the broken connection so Django reconnects fresh,
# and retry. BaseException (not Exception) because NFSN's SSL socket raises
# KeyboardInterrupt on EINTR during long runs.
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP_SECONDS = 5

SMOKE_SAMPLE_SIZE = 10

SYSTEM_PROMPT = (
    "You are a careful editorial assistant for a public archive of real, "
    "published appellate court opinions. You read the text of a SINGLE "
    "opinion and summarize only its holding -- what the court actually "
    "decided, and the essential reason it gave.\n\n"
    "Strict rules:\n"
    "- Use the court's own language wherever possible; minimize paraphrase.\n"
    "- 2 to 3 sentences maximum. Plain text. No headings, no preamble.\n"
    "- Do NOT add interpretation, legal analysis, significance, commentary, "
    "or any outside material. Do NOT predict outcomes or give advice.\n"
    "- Stay strictly bounded to what THIS opinion's text says. If the text "
    "does not state a clear holding (e.g. it is a brief order or a fragment), "
    "say so in one short sentence rather than inventing one.\n"
    "- Never cite or mention cases, statutes, or facts that do not appear in "
    "the provided text."
)

USER_TEMPLATE = (
    "Opinion text:\n\n{body}\n\n"
    "Summarize the holding(s) of this opinion under the rules you were given. "
    "Then list the paragraph number(s) where the holding is stated, as a JSON "
    "array of integers -- use the court's own bracketed paragraph markers "
    "(e.g. [¶12]) when present; otherwise output an empty array []. "
    "Respond in EXACTLY this format and nothing else:\n"
    "HOLDING: <text>\n"
    "PARAGRAPHS: <json array>"
)

# Parse the model's "HOLDING: ...\nPARAGRAPHS: [...]" reply. Tolerant of
# casing and surrounding whitespace; the PARAGRAPHS array is optional.
_RESP_RE = re.compile(
    r"HOLDING:\s*(?P<holding>.*?)\s*(?:PARAGRAPHS:\s*(?P<paras>\[.*?\]))?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _parse_response(text: str) -> tuple[str, list[int]]:
    """Pull (holding_text, [paragraph ints]) out of the model's reply.

    Defensive: if the strict format is missing, fall back to treating the
    whole reply (minus any HOLDING: label) as the holding with no paragraphs.
    Non-integer paragraph entries are dropped.
    """
    text = (text or "").strip()
    if not text:
        return "", []

    holding = ""
    paras: list[int] = []

    m = _RESP_RE.search(text)
    if m and m.group("holding") is not None:
        holding = m.group("holding").strip()
        raw_paras = m.group("paras")
        if raw_paras:
            try:
                parsed = json.loads(raw_paras)
                if isinstance(parsed, list):
                    for p in parsed:
                        if isinstance(p, bool):
                            continue
                        if isinstance(p, int):
                            paras.append(p)
                        elif isinstance(p, str) and p.strip().isdigit():
                            paras.append(int(p.strip()))
            except (ValueError, TypeError):
                paras = []
    else:
        # No recognizable format -- strip a leading "HOLDING:" if present and
        # keep the rest. A separate PARAGRAPHS: line, if any, is sliced off.
        cleaned = re.sub(r"^\s*HOLDING:\s*", "", text, flags=re.IGNORECASE)
        cleaned = re.split(r"\bPARAGRAPHS:\s*", cleaned, flags=re.IGNORECASE)[0]
        holding = cleaned.strip()

    # De-duplicate paragraphs, preserve order.
    seen = set()
    deduped = []
    for p in paras:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return holding, deduped


def _call_anthropic(body: str, model: str, api_key: str) -> tuple[str, int, int]:
    """POST one opinion to the Messages API; return (text, in_tokens, out_tokens).

    Surfaces the API error body on non-2xx so a 400 (bad model name, oversized
    request, etc.) shows its reason instead of a bare status code. Raises on
    HTTP error so the caller's retry loop can back off.
    """
    response = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": USER_TEMPLATE.format(body=body)},
            ],
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        snippet = response.text[:500].replace("\n", " ")
        # Attach retry-after (if any) so the caller can honor it.
        err = requests.HTTPError(
            f"Anthropic API {response.status_code} {response.reason}: {snippet}"
        )
        err.status_code = response.status_code  # type: ignore[attr-defined]
        err.retry_after = response.headers.get("retry-after")  # type: ignore[attr-defined]
        raise err

    payload = response.json()
    parts = payload.get("content", [])
    text = "".join(
        block.get("text", "") for block in parts if block.get("type") == "text"
    )
    usage = payload.get("usage", {}) or {}
    return text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


class Command(BaseCommand):
    help = "Extract a summarized holding per opinion via Claude Haiku (NH-first)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state",
            default=None,
            help="USPS 2-letter state code (e.g. NH). Required -- this pass is "
                 "NH-only per the proving-ground rule.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N opinions (test-batch convenience).",
        )
        parser.add_argument(
            "--smoke",
            action="store_true",
            help=f"Run against {SMOKE_SAMPLE_SIZE} random opinions, PRINT the "
                 "output, and commit NOTHING. For eyeballing prompt quality "
                 "and projecting full-batch cost before the real run.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-extract even where a holding already exists (e.g. after "
                 "tuning the prompt). Never overwrites a human-REVIEWED holding.",
        )
        parser.add_argument(
            "--model",
            default=DEFAULT_MODEL,
            help=f"Anthropic model id (default {DEFAULT_MODEL}).",
        )
        parser.add_argument(
            "--rpm",
            type=int,
            default=DEFAULT_RPM,
            help=f"Target requests/minute (default {DEFAULT_RPM}). The API's "
                 "429 + retry-after is the real limiter; raise if your tier allows.",
        )
        parser.add_argument(
            "--max-input-chars",
            type=int,
            default=DEFAULT_MAX_INPUT_CHARS,
            help=f"Truncate opinion text to this many chars (default "
                 f"{DEFAULT_MAX_INPUT_CHARS:,} ~= 6K tokens). Holdings sit early "
                 "in the opinion, so truncation is safe and cuts cost.",
        )
        parser.add_argument(
            "--max-runtime",
            type=int,
            default=0,
            help="Stop cleanly after ~N seconds, leaving the rest for the next "
                 "run (resumes via the holding_summary='' filter). 0 = run to "
                 "completion (default).",
        )

    def handle(self, *args, state, limit, smoke, force, model, rpm,
               max_input_chars, max_runtime, **options):
        from opinions.models import Opinion, State

        if not state:
            raise CommandError(
                "--state is required (this pass is NH-only). e.g. --state NH"
            )
        state = state.upper()
        try:
            state_obj = State.objects.get(code=state)
        except State.DoesNotExist:
            raise CommandError(f"State {state!r} not found.")

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise CommandError(
                "ANTHROPIC_API_KEY not set. Add it to your environment (.env on "
                "NFSN):\n    ANTHROPIC_API_KEY=sk-ant-...\n"
                "Holdings extraction is the only feature that needs it."
            )

        # Pre-resolve court ids to skip the court__state JOIN (CLAUDE.md gotcha).
        court_ids = list(state_obj.courts.values_list("id", flat=True))
        if not court_ids:
            raise CommandError(f"No courts found for state {state!r}.")

        # Base queue: this state's opinions that have body text. --force drops
        # the "no summary yet" condition but ALWAYS protects human-REVIEWED
        # holdings from being clobbered.
        qs = (
            Opinion.objects.filter(court_id__in=court_ids)
            .exclude(raw_text="")
            .exclude(holding_review_status=Opinion.ReviewStatus.REVIEWED)
        )
        if not force:
            qs = qs.filter(holding_summary="")
        qs = qs.only("id", "case_number", "raw_text")

        if smoke:
            return self._run_smoke(qs, model, api_key, max_input_chars, state_obj)

        return self._run_batch(
            qs, limit, model, api_key, rpm, max_input_chars, max_runtime
        )

    # ------------------------------------------------------------------
    # Smoke test -- print, don't write.
    # ------------------------------------------------------------------
    def _run_smoke(self, qs, model, api_key, max_input_chars, state_obj):
        sample = list(qs.order_by("?")[:SMOKE_SAMPLE_SIZE])
        if not sample:
            self.stdout.write(self.style.WARNING(
                "Nothing to sample -- every opinion already has a holding "
                "(or none have body text). Try --force to re-sample."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"SMOKE TEST -- {len(sample)} random {state_obj.code} opinions via "
            f"{model}. Nothing will be written.\n"
        ))

        in_tokens = out_tokens = 0
        ok = 0
        for op in sample:
            body = (op.raw_text or "")[:max_input_chars]
            try:
                text, ti, to = _call_anthropic(body, model, api_key)
            except Exception as exc:  # noqa: BLE001 -- smoke: report and move on
                self.stdout.write(self.style.ERROR(
                    f"\n[{op.case_number}] API error: {type(exc).__name__}: {exc}"
                ))
                continue
            in_tokens += ti
            out_tokens += to
            ok += 1
            holding, paras = _parse_response(text)
            self.stdout.write("\n" + "=" * 70)
            self.stdout.write(self.style.HTTP_INFO(
                f"{op.case_number}  (tokens in={ti} out={to})"
            ))
            self.stdout.write(f"HOLDING:    {holding or '(empty)'}")
            self.stdout.write(f"PARAGRAPHS: {paras}")
            time.sleep(0.5)

        if not ok:
            self.stdout.write(self.style.ERROR("\nNo successful calls."))
            return

        avg_in = in_tokens / ok
        avg_out = out_tokens / ok
        sample_cost = (in_tokens / 1e6 * PRICE_IN_PER_M_USD
                       + out_tokens / 1e6 * PRICE_OUT_PER_M_USD)

        # Project the full pending corpus (re-count without the random slice).
        remaining = (
            Opinion.objects.filter(
                court_id__in=list(state_obj.courts.values_list("id", flat=True))
            )
            .exclude(raw_text="")
            .exclude(holding_review_status=Opinion.ReviewStatus.REVIEWED)
            .filter(holding_summary="")
            .count()
        )
        proj_cost = (
            remaining * (avg_in / 1e6 * PRICE_IN_PER_M_USD
                         + avg_out / 1e6 * PRICE_OUT_PER_M_USD)
        )

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("SMOKE SUMMARY"))
        self.stdout.write(
            f"  successful calls : {ok}/{len(sample)}\n"
            f"  avg tokens       : in={avg_in:,.0f}  out={avg_out:,.0f}\n"
            f"  sample cost      : ${sample_cost:.4f}\n"
            f"  pending corpus   : {remaining:,} opinions\n"
            f"  PROJECTED FULL   : ~${proj_cost:,.2f}  ({state_obj.code})"
        )
        if proj_cost > 200:
            self.stdout.write(self.style.WARNING(
                "  Projected cost exceeds $200 -- confirm before the full run."
            ))

    # ------------------------------------------------------------------
    # Real batch -- write each summary as it comes back.
    # ------------------------------------------------------------------
    def _run_batch(self, qs, limit, model, api_key, rpm, max_input_chars,
                   max_runtime):
        from opinions.models import Opinion

        total = qs.count()
        if limit:
            total = min(total, limit)
        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                "Nothing to do -- every opinion already has a holding."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Extracting holdings for {total:,} opinions via {model}."
        ))
        self.stdout.write(
            f"  price=${PRICE_IN_PER_M_USD:.0f}/M in, "
            f"${PRICE_OUT_PER_M_USD:.0f}/M out  target_rpm={rpm}  "
            f"max_input_chars={max_input_chars:,}\n"
        )

        seconds_between = 60.0 / rpm if rpm > 0 else 0.0
        last_call = 0.0
        done = ok = failed = empty = 0
        in_tokens = out_tokens = 0
        run_started = time.time()
        deadline = run_started + max_runtime if max_runtime else None

        # Iterate lazily in PK order so a kill + restart resumes from the
        # filter, not from a stale offset. iterator() avoids loading all rows.
        for op in qs.order_by("id").iterator(chunk_size=200):
            if limit and done >= limit:
                break
            if deadline is not None and time.time() >= deadline:
                self.stdout.write(
                    f"Reached --max-runtime; stopping cleanly after {done:,} "
                    "this run. Re-run to resume."
                )
                break

            # Rate limit.
            elapsed = time.time() - last_call
            if elapsed < seconds_between:
                time.sleep(seconds_between - elapsed)

            body = (op.raw_text or "")[:max_input_chars]
            text = self._call_with_retry(body, model, api_key)
            last_call = time.time()
            done += 1
            if text is None:
                failed += 1
                continue

            ti, to, raw = text
            in_tokens += ti
            out_tokens += to
            holding, paras = _parse_response(raw)
            if not holding:
                # Model returned nothing usable; leave the row in the queue to
                # retry on a future run rather than storing an empty summary.
                empty += 1
                continue

            if self._persist(op.pk, holding, paras, model):
                ok += 1
            else:
                failed += 1

            if done % 25 == 0 or done == total:
                cost = (in_tokens / 1e6 * PRICE_IN_PER_M_USD
                        + out_tokens / 1e6 * PRICE_OUT_PER_M_USD)
                rate = done / max(time.time() - run_started, 0.001)
                eta = (total - done) / max(rate, 0.001)
                self.stdout.write(
                    f"  [{done:>6,}/{total:,}] ok={ok} empty={empty} "
                    f"fail={failed}  tokens(in/out)={in_tokens:,}/{out_tokens:,}  "
                    f"cost=${cost:.2f}  eta={eta/60:.0f}min"
                )

        cost = (in_tokens / 1e6 * PRICE_IN_PER_M_USD
                + out_tokens / 1e6 * PRICE_OUT_PER_M_USD)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done in {(time.time() - run_started)/60:.1f} min. "
            f"Wrote {ok:,} holdings, {empty:,} empty/skipped, {failed:,} failed. "
            f"Tokens in/out={in_tokens:,}/{out_tokens:,}, ~${cost:.2f}."
        ))

    def _call_with_retry(self, body, model, api_key):
        """Call the API with backoff; return (in_tokens, out_tokens, text) or None."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                text, ti, to = _call_anthropic(body, model, api_key)
                return ti, to, text
            except BaseException as exc:  # noqa: BLE001 -- NFSN SSL raises KeyboardInterrupt
                status = getattr(exc, "status_code", None)
                # 4xx other than 429 won't fix on retry -- give up on this row.
                if status is not None and status != 429 and 400 <= status < 500:
                    self.stderr.write(self.style.ERROR(
                        f"  non-retryable {status}: {exc}"
                    ))
                    return None
                if attempt >= MAX_RETRIES:
                    self.stderr.write(self.style.ERROR(
                        f"  API failed {MAX_RETRIES}x ({type(exc).__name__}: "
                        f"{exc}); skipping this opinion."
                    ))
                    return None
                # Honor retry-after when present, else exponential backoff.
                retry_after = getattr(exc, "retry_after", None)
                if retry_after:
                    try:
                        sleep_s = float(retry_after)
                    except (TypeError, ValueError):
                        sleep_s = RETRY_BASE_SLEEP_SECONDS * (2 ** (attempt - 1))
                else:
                    sleep_s = RETRY_BASE_SLEEP_SECONDS * (2 ** (attempt - 1))
                sleep_s = min(sleep_s, RETRY_MAX_SLEEP_SECONDS)
                self.stderr.write(self.style.WARNING(
                    f"  API error (attempt {attempt}/{MAX_RETRIES}) "
                    f"{type(exc).__name__}: {exc}; sleeping {sleep_s:.0f}s..."
                ))
                time.sleep(sleep_s)
        return None

    def _persist(self, pk, holding, paras, model):
        """Write the 7 holding fields with retry-on-reconnect.

        Uses a targeted ``.update()`` -- NOT ``Opinion.save()`` -- so we don't
        re-trigger the parser save-hook, and we touch only editorial metadata.
        Retries on NFSN's SSL connection drops (errno 2013 / interrupted
        socket), same defense as embed_opinions.
        """
        from opinions.models import Opinion

        fields = {
            "holding_summary": holding,
            "holding_source_paras": paras,
            "holding_review_status": Opinion.ReviewStatus.AI_ONLY,
            "holding_reviewed_by": "",
            "holding_reviewed_at": None,
            "holding_extracted_at": timezone.now(),
            "holding_model": model,
        }
        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                Opinion.objects.filter(pk=pk).update(**fields)
                return True
            except BaseException as exc:  # noqa: BLE001
                if attempt >= DB_MAX_RETRIES:
                    self.stderr.write(self.style.ERROR(
                        f"  DB persist failed {DB_MAX_RETRIES}x for opinion "
                        f"{pk}: {type(exc).__name__}: {exc}"
                    ))
                    return False
                self.stderr.write(self.style.WARNING(
                    f"  DB error (attempt {attempt}/{DB_MAX_RETRIES}) "
                    f"{type(exc).__name__}: {exc}; reconnecting..."
                ))
                try:
                    connection.close()
                except BaseException:
                    pass
                time.sleep(DB_RETRY_SLEEP_SECONDS)
        return False
