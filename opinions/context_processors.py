"""Template context processors for DocketDrift.

Anything every page needs in its template context lands here so we don't
have to thread it through every view. Registered in
``docketdrift_site.settings.TEMPLATES.OPTIONS.context_processors``.
"""
from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)


# Sidebar tag-cloud taxonomy. Plain-language but *legally specific* phrases
# that actually appear in MN appellate opinions -- not generic words like
# "Murder" that don't help narrow a search. Click on a chip and the search
# box runs ``?q=<phrase>`` against ``raw_text``. The cloud sizes each chip
# by how often it actually occurs in the current state's corpus (see
# ``_get_sized_tags`` below), so big chips = high signal in real data and
# tags with zero hits drop out so the cloud is always honest.
EXPLORE_TAGS: tuple[str, ...] = (
    "unsigned orders",
    "civil contempt",
    "ineffective assistance",
    "search and seizure",
    "summary judgment",
    "post-conviction relief",
    "termination of parental rights",
    "harassment restraining order",
    "evidentiary hearing",
    "implied consent",
    "sentencing departure",
    "controlled substance",
    "Fourth Amendment",
    "Fifth Amendment",
    "due process",
    "Miranda",
    "spoliation",
    "habeas corpus",
    "abuse of discretion",
    "preponderance of the evidence",
)


# Disposition color taxonomy displayed as a legend in the sidebar so users
# always have the key for the colored pills visible. ``slug`` matches the
# ``disposition-<slug>`` CSS class and ``Opinion.disposition_bucket`` field
# (so clicking a legend chip filters the list to that bucket); ``label`` is
# the human-facing legend text.
DISPOSITION_BUCKETS: tuple[tuple[str, str], ...] = (
    ("affirmed", "Affirmed"),
    ("reversed", "Reversed"),
    ("vacated", "Vacated"),
    ("remanded", "Remanded"),
    ("mixed", "Mixed"),
    ("modified", "Modified"),
    ("dismissed", "Dismissed"),
    ("granted", "Granted"),
    ("denied", "Denied"),
)


# Font-size buckets for the tag cloud, ordered from rarest (xs) to most
# frequent (xl). The CSS rule for each is in docketdrift.css.
_SIZE_BUCKETS = ("xs", "sm", "md", "lg", "xl")
_CACHE_TTL_SECONDS = 60 * 60 * 2  # 2 hr -- tag counts change slowly; the
# cold-cache cost of 20+ MATCH-COUNTs once an hour is plenty. Pre-warming
# via the precompute_explore_tags command (run via NFSN scheduled task
# every hour) means this TTL effectively never expires for real users.
# When _CACHE_TTL_SECONDS WAS 15 min, the cold-cache window happened
# multiple times per state per hour and was the residual cause of slow
# state-landing first hits.


# Per-tag self-bound for the WARMER's FULLTEXT counts. Generous (this is
# background work) but finite, so one pathological tag on a huge corpus
# can't stall the whole warm run.
_WARM_PER_TAG_TIMEOUT_SECONDS = 10
# Whole-state budget for the warmer. LA (341K rows) can't finish 20
# corpus-scale MATCH-COUNTs in any reasonable time; past this we keep
# whatever counts we got and move on to the next state rather than
# letting one state starve the others.
_WARM_TOTAL_BUDGET_SECONDS = 120
# Short TTL for a partial/empty result, so a degraded state retries on the
# next warm run instead of being stuck with an empty cloud for 2 hours.
_PARTIAL_CACHE_TTL_SECONDS = 15 * 60


def _count_tag_bounded(tag, court_ids, timeout_seconds):
    """One FULLTEXT phrase COUNT, self-bounded, degrading to None.

    Returns the count, or ``None`` if the query was killed / errored.

    Self-binding matters for the same reason it does in
    ``semantic._run_vector_query`` and ``views._fulltext_candidate_ids``:
    a query KILLed at the statement timeout leaves the POOLED connection
    interrupted (errno 188/1969), and the next request to reuse it 500s on
    whatever it runs. So on any failure we close the connection rather
    than hand a poisoned one back to the pool. ``BaseException`` (not
    ``Exception``) because NFSN's SSL socket surfaces an EINTR'd read as
    ``KeyboardInterrupt``.
    """
    if connection.vendor != "mysql":
        from opinions.models import Opinion
        return Opinion.objects.filter(
            court_id__in=court_ids, raw_text__icontains=tag
        ).count()

    placeholders = ",".join(["%s"] * len(court_ids))
    # BOOLEAN MODE + quoted phrase = exact-phrase match against the
    # FULLTEXT index; the quoting is what makes multi-word tags like
    # "Fourth Amendment" match precisely. Raw SQL (not .extra()) so the
    # SET STATEMENT prefix rides on the same statement.
    sql = (
        f"SET STATEMENT max_statement_time={int(timeout_seconds)} FOR "
        f"SELECT COUNT(*) FROM opinions_opinion "
        f"WHERE court_id IN ({placeholders}) "
        f"AND MATCH(raw_text, title) AGAINST (%s IN BOOLEAN MODE)"
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, list(court_ids) + [f'"{tag}"'])
            row = cursor.fetchone()
        return row[0] if row else 0
    except BaseException:
        try:
            connection.close()
        except BaseException:
            pass
        return None


def _get_sized_tags(state, compute: bool = False) -> list[tuple[str, int, str]]:
    """Return ``(tag, count, size_bucket)`` for the current state's corpus.

    **Read-only from the request path.** ``compute`` defaults to False, so
    a cold cache returns ``[]`` immediately and the page renders without a
    tag cloud. ONLY ``precompute_explore_tags`` passes ``compute=True``.

    This split is the fix for a whole class of outage (2026-08-18). The
    sizing needs ~20 corpus-scale FULLTEXT MATCH-COUNTs, and this function
    runs in a CONTEXT PROCESSOR -- i.e. on every templated response. When
    the cache went cold (TTL expiry, gunicorn restart, a newly-added
    state), the next request would try to compute all 20 inline. On a
    341K-row corpus like Louisiana each count exceeds the 25s web cap, so
    the request spent ~500 seconds running queries that were each KILLed
    in turn, poisoning a pooled connection every time, while the caller
    saw only a hang. That presented as "the whole subdomain is down."

    Computing this in a request was never safe -- it was merely fast
    enough to hide on a 20-60K corpus. Now the request path cannot do it
    at all: warm cache or no cloud. The cloud is a browse affordance, not
    load-bearing content, so degrading it is strictly better than
    degrading the page.
    """
    cache_key = f"explore_tags_sized:{state.code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    if not compute:
        # Cold cache in a request: render without the cloud. The hourly
        # precompute_explore_tags task fills it. Do NOT "just this once"
        # compute here -- see the docstring.
        return []

    # ---- Warmer path only, from here down. ----
    # Pre-resolve court_ids once. Without this every per-tag count JOINs
    # opinions -> courts -> states; pre-resolved IDs let MariaDB use the
    # FULLTEXT index AND the court_id index without the multi-table plan.
    court_ids = list(state.courts.values_list("id", flat=True))
    if not court_ids:
        cache.set(cache_key, [], _CACHE_TTL_SECONDS)
        return []

    # Counts ACCUMULATE across warm runs, resuming from a stored cursor.
    #
    # Without this, a state too big to count inside one budget would be
    # stuck forever: every run starts at EXPLORE_TAGS[0], burns the whole
    # budget on the same first few tags, and the cloud never grows past
    # them. (Measured on LA: 2 of 20 tags per 120s run.) Resuming means
    # each run advances through the list and the cloud fills in over
    # successive runs, wrapping around to refresh the oldest counts.
    counts_key = f"explore_tags_counts:{state.code}"
    cursor_key = f"explore_tags_cursor:{state.code}"
    known: dict[str, int] = dict(cache.get(counts_key) or {})
    start = int(cache.get(cursor_key) or 0) % len(EXPLORE_TAGS)

    started = time.monotonic()
    degraded = False
    processed = 0
    idx = start
    for _ in range(len(EXPLORE_TAGS)):
        if time.monotonic() - started > _WARM_TOTAL_BUDGET_SECONDS:
            logger.warning(
                "explore_tags: %s hit the %ss warm budget after %d/%d tags "
                "this run (resuming at index %d next run; %d tags known)",
                state.code, _WARM_TOTAL_BUDGET_SECONDS, processed,
                len(EXPLORE_TAGS), idx, len(known),
            )
            degraded = True
            break
        tag = EXPLORE_TAGS[idx]
        n = _count_tag_bounded(tag, court_ids, _WARM_PER_TAG_TIMEOUT_SECONDS)
        if n is None:
            logger.warning(
                "explore_tags: count for %r timed out/failed on %s",
                tag, state.code,
            )
            degraded = True
        else:
            # Record zeros too, so a genuinely-absent tag isn't retried
            # every run; it's filtered out of the cloud below.
            known[tag] = n
        processed += 1
        idx = (idx + 1) % len(EXPLORE_TAGS)

    # Persist accumulated counts + where to resume. Long TTL on the raw
    # counts so progress survives even when the built cloud expires.
    cache.set(counts_key, known, _CACHE_TTL_SECONDS * 12)
    cache.set(cursor_key, idx, _CACHE_TTL_SECONDS * 12)

    raw_counts: list[tuple[str, int]] = [
        (tag, n) for tag, n in known.items() if n > 0
    ]

    if not raw_counts:
        # Cache the empty result briefly so a degraded state retries next
        # run rather than serving an empty cloud for the full TTL.
        cache.set(
            cache_key, [],
            _PARTIAL_CACHE_TTL_SECONDS if degraded else _CACHE_TTL_SECONDS,
        )
        return []

    counts_only = [n for _, n in raw_counts]
    lo, hi = min(counts_only), max(counts_only)
    span = max(hi - lo, 1)

    sized: list[tuple[str, int, str]] = []
    for tag, n in raw_counts:
        # Map count into 0..1 then bucket into one of the 5 size slots.
        ratio = (n - lo) / span
        idx = min(int(ratio * len(_SIZE_BUCKETS)), len(_SIZE_BUCKETS) - 1)
        sized.append((tag, n, _SIZE_BUCKETS[idx]))

    # Sort largest-first so the cloud reads "most common at the top".
    sized.sort(key=lambda x: (-x[1], x[0].lower()))

    # A partial result gets the short TTL so the next warm run retries the
    # tags that timed out, instead of freezing a half-built cloud for 2hr.
    cache.set(
        cache_key, sized,
        _PARTIAL_CACHE_TTL_SECONDS if degraded else _CACHE_TTL_SECONDS,
    )
    return sized


def site_extras(request):
    """Inject site-wide constants + per-state tag cloud sizing.

    Wrapped in try/except so a failure inside the tag computation NEVER
    propagates to the template render. If it crashes, the sidebar tag
    cloud silently hides; everything else (search, opinion list, etc.)
    keeps working.
    """
    state = getattr(request, "state", None)
    try:
        tags = _get_sized_tags(state) if state is not None else []
    except Exception:
        logger.warning("site_extras: explore-tags computation failed; rendering empty", exc_info=True)
        tags = []
    return {
        "EXPLORE_TAGS": tags,
        "DISPOSITION_BUCKETS": DISPOSITION_BUCKETS,
    }
