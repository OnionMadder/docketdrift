"""Canonical form for a docket number.

Shared by `normalize_case_numbers` (which rewrites stored values) and the
opinion views (which canonicalize an incoming URL so links minted before the
rewrite still resolve). Both MUST use the same function or a renamed row
becomes unreachable by its old URL.

DELIBERATELY CONSERVATIVE. Only two transformations are applied, and only the
shapes below -- both verified against a full-corpus dry run on 2026-08-04:

  1. Strip a leading "No. " / "Nos. "   'No. A15-1566' -> 'A15-1566'
  2. Minnesota appellate numbering       'a230380'      -> 'A23-0380'
                                         'A15-178'      -> 'A15-0178'

Everything else is returned unchanged. That is not laziness -- the large
"other" bucket is full of REAL docket numbers whose format is simply
state- and era-specific:

    1 CA-CV 09-0595   (AZ Court of Appeals, Division One)
    2 CA-SA 2026-0048 (AZ Division Two, 4-digit year)
    CX-84-550         (MN pre-2000 numbering)
    2014-0369, 4653   (NH modern and historic)
    16342-SA          (AZ special action)

Rewriting those would corrupt ~83,000 correct records. `cl-<id>` values are
also left alone: those digits are CourtListener cluster ids with no docket
number recoverable from the row, so they need an API lookup, not a regex.
"""
import re

_LEAD_NO = re.compile(r"^N[Oo][Ss]?\.?\s+(.*)$")
_MN_STEM = re.compile(r"^([aA])(\d{2})(\d{4})$")
_MN_UNPADDED = re.compile(r"^([A-Z])(\d{2})-(\d{1,3})$")
_SYNTHETIC = re.compile(r"^cl-\d+$", re.I)


def canonical_case_number(raw):
    """Return the canonical form of `raw`, or `raw` unchanged.

    Idempotent: canonical(canonical(x)) == canonical(x).
    """
    s = (raw or "").strip()
    if not s or _SYNTHETIC.match(s):
        return s

    m = _MN_STEM.match(s)
    if m:
        return "%s%s-%s" % (m.group(1).upper(), m.group(2), m.group(3))

    m = _LEAD_NO.match(s)
    if m:
        s = m.group(1).strip()

    m = _MN_UNPADDED.match(s)
    if m:
        s = "%s%s-%s" % (m.group(1), m.group(2), m.group(3).zfill(4))

    return s


def is_normalizable(raw):
    """True when canonicalizing would actually change the value."""
    s = (raw or "").strip()
    return bool(s) and canonical_case_number(s) != s
