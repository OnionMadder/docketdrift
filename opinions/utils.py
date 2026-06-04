"""Cross-cutting helpers shared across opinions code."""
from __future__ import annotations

import re


# Patterns for normalizing Minnesota appellate docket numbers.
#
# CourtListener stores them undashed (``A251191``) and sometimes
# inconsistently cased (``a250872``); the published / cited form is
# dashed and uppercase (``A25-1191``). We canonicalize to the dashed
# uppercase form everywhere ``case_number`` is stored so the column is
# consistent across the CL cron, the manual-upload pipeline, and the
# eventual bulk-data import.
#
# Order matters: more specific prefixes (``ADM``) come before the
# single-letter prefix to avoid false matches. The patterns are
# anchored end-to-end so we don't silently rewrite substrings of an
# unfamiliar format.
_DOCKET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^ADM(\d{2})-?(\d{4})$"), r"ADM\1-\2"),
    (re.compile(r"^([A-Z])(\d{2})-?(\d{4})$"), r"\1\2-\3"),
    (re.compile(r"^C(\d{1,2})-?(\d{2})-?(\d{4})$"), r"C\1-\2-\3"),
)


def compute_disposition_bucket(disposition: str | None) -> str:
    """Return the CSS / filter bucket slug for a free-form disposition string.

    Used at ``Opinion.save()`` time to populate ``Opinion.disposition_bucket``
    so the column is indexable + filterable. Also re-used in the data
    migration that backfills existing rows.

    Buckets and the precedence order (longer / more specific phrases first):

    - ``mixed``     -- "Affirmed in part, reversed in part, ..."
    - ``vacated``   -- contains "vacated"
    - ``reversed``  -- contains "reversed" (incl. "reversed and remanded")
    - ``remanded``  -- starts with "Remanded"
    - ``affirmed``  -- starts with "Affirmed"
    - ``modified``  -- starts with "Modified"
    - ``dismissed`` -- starts with "Dismissed" / "Stayed" / "Reinstated"
    - ``granted``   -- starts with "Granted"
    - ``denied``    -- starts with "Denied"
    - ``other``     -- something recognizable but not bucketed
    - ``""``        -- no disposition at all
    """
    d = (disposition or "").lower().strip()
    if not d:
        return ""
    if "in part" in d:
        return "mixed"
    if "vacated" in d:
        return "vacated"
    if "reversed" in d:
        return "reversed"
    if d.startswith("remanded"):
        return "remanded"
    if d.startswith("affirmed"):
        return "affirmed"
    if d.startswith("modified"):
        return "modified"
    if d.startswith("dismissed") or d.startswith("stayed") or d.startswith("reinstated"):
        return "dismissed"
    if d.startswith("granted"):
        return "granted"
    if d.startswith("denied"):
        return "denied"
    return "other"


def normalize_docket_number(s: str | None) -> str:
    """Return the canonical dashed-uppercase form of an MN docket number.

    Handles the appellate formats we've observed in real opinions:

    - ``A##-####``           Court of Appeals (most common)
    - ``ADM##-####``         Supreme Court administrative orders
    - ``C#-##-####``         Older numbering (mid-1990s and earlier)

    Returns the input uppercased + stripped (but otherwise unchanged) if
    it doesn't match a known format, so unknown values are never silently
    corrupted -- they just stay un-normalized and are visible in admin.
    """
    if not s:
        return s or ""
    cleaned = s.strip().upper()
    for pattern, replacement in _DOCKET_PATTERNS:
        if pattern.match(cleaned):
            return pattern.sub(replacement, cleaned)
    return cleaned
