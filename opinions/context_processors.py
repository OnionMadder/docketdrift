"""Template context processors for DocketDrift.

Anything every page needs in its template context lands here so we don't
have to thread it through every view. Registered in
``docketdrift_site.settings.TEMPLATES.OPTIONS.context_processors``.
"""
from __future__ import annotations

from django.core.cache import cache


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
_CACHE_TTL_SECONDS = 60 * 15  # 15 min -- balance freshness vs. cold-cache cost.


def _get_sized_tags(state) -> list[tuple[str, int, str]]:
    """Return ``(tag, count, size_bucket)`` for the current state's corpus.

    Counts are ``Opinion.raw_text__icontains=tag`` against the state's
    opinions, cached per-state for 15 minutes since the cron only changes
    the corpus a couple of times per week. Tags with zero hits are
    filtered out so the cloud never lies about coverage.
    """
    cache_key = f"explore_tags_sized:{state.code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Local import: this module is loaded at template-context build time,
    # before Django apps are guaranteed-ready in some startup paths.
    from opinions.models import Opinion

    raw_counts: list[tuple[str, int]] = []
    for tag in EXPLORE_TAGS:
        n = Opinion.objects.filter(
            court__state=state,
            raw_text__icontains=tag,
        ).count()
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
    """Inject site-wide constants + per-state tag cloud sizing."""
    state = getattr(request, "state", None)
    tags = _get_sized_tags(state) if state is not None else []
    return {
        "EXPLORE_TAGS": tags,
        "DISPOSITION_BUCKETS": DISPOSITION_BUCKETS,
    }
