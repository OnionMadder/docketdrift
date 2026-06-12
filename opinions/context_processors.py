"""Template context processors for DocketDrift.

Anything every page needs in its template context lands here so we don't
have to thread it through every view. Registered in
``docketdrift_site.settings.TEMPLATES.OPTIONS.context_processors``.
"""
from __future__ import annotations

import logging

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


def _get_sized_tags(state) -> list[tuple[str, int, str]]:
    """Return ``(tag, count, size_bucket)`` for the current state's corpus.

    Uses MariaDB's FULLTEXT index (added by migration 0012) when available
    -- each per-tag count returns in milliseconds against the 60K-row
    corpus. Falls back to ``raw_text__icontains`` on SQLite / other
    backends; slow on big corpora but fine for local dev with small data.

    Cached per-state for 15 minutes since the cron only changes the corpus
    a couple of times per week. The whole computation is wrapped in
    try/except: if any per-tag query fails for any reason (e.g. FULLTEXT
    stopword-only tag, transient DB error), we log + skip and continue;
    if the entire computation explodes, we return an empty cloud rather
    than crash the page.
    """
    cache_key = f"explore_tags_sized:{state.code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Local import: this module is loaded at template-context build time,
    # before Django apps are guaranteed-ready in some startup paths.
    from opinions.models import Opinion

    use_fulltext = connection.vendor == "mysql"

    # Pre-resolve court_ids once per call. Without this every per-tag
    # MATCH-AGAINST COUNT JOINed opinions -> courts -> states and N tags
    # * full-corpus FULLTEXT scan was THE single biggest source of state-
    # landing slowness -- this runs on every templated response, so a
    # crawler hammering /tag/ + /opinions/ + state landings could pile
    # 60+ JOIN-COUNTs on the workers concurrently. Pre-resolved court
    # IDs let MariaDB use the FULLTEXT index AND the court_id index
    # without dragging in the multi-table join plan.
    court_ids = list(state.courts.values_list("id", flat=True))

    raw_counts: list[tuple[str, int]] = []
    for tag in EXPLORE_TAGS:
        try:
            if use_fulltext:
                # BOOLEAN MODE + quoted phrase = exact-phrase match against
                # the FULLTEXT index. Phrase quoting (" ... ") is what makes
                # multi-word tags like "Fourth Amendment" match precisely.
                # extra() is the cleanest way to inject MATCH AGAINST in
                # Django -- there's no native ORM lookup for MySQL FULLTEXT.
                n = (
                    Opinion.objects.filter(court_id__in=court_ids)
                    .extra(
                        where=[
                            "MATCH(opinions_opinion.raw_text, opinions_opinion.title) "
                            "AGAINST (%s IN BOOLEAN MODE)"
                        ],
                        params=[f'"{tag}"'],
                    )
                    .values("pk")
                    .count()
                )
            else:
                n = Opinion.objects.filter(
                    court_id__in=court_ids,
                    raw_text__icontains=tag,
                ).count()
        except Exception:
            logger.warning("explore_tags: per-tag count failed for %r", tag, exc_info=True)
            continue
        if n > 0:
            raw_counts.append((tag, n))

    if not raw_counts:
        cache.set(cache_key, [], _CACHE_TTL_SECONDS)
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

    cache.set(cache_key, sized, _CACHE_TTL_SECONDS)
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
