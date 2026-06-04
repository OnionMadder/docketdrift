"""Template context processors for DocketDrift.

Anything every page needs in its template context lands here so we don't
have to thread it through every view. Registered in
``docketdrift_site.settings.TEMPLATES.OPTIONS.context_processors``.
"""
from __future__ import annotations


# Sidebar tag-cloud taxonomy. Plain-English terms a non-lawyer might want
# to click into, mapped (roughly) to what actually appears in MN appellate
# opinion text. Each tag pre-fills the search box via ?q=<tag>. v2: replace
# with a dynamic top-N count from Opinion.raw_text.
EXPLORE_TAGS: tuple[str, ...] = (
    "Murder",
    "DWI",
    "Drugs",
    "Theft",
    "Sentencing",
    "Custody",
    "Divorce",
    "Eviction",
    "Search and seizure",
    "Discrimination",
    "Negligence",
    "Contract",
)


# Disposition color taxonomy displayed as a legend in the sidebar so users
# always have the key for the colored pills visible. ``slug`` matches the
# ``disposition-<slug>`` CSS class on case-status pills; ``label`` is the
# human-facing legend text.
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


def site_extras(request):
    """Inject site-wide constants (tag cloud, disposition legend)."""
    return {
        "EXPLORE_TAGS": EXPLORE_TAGS,
        "DISPOSITION_BUCKETS": DISPOSITION_BUCKETS,
    }
