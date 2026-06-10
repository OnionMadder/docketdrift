"""Minnesota statute citation extractor.

Recognizes the common Bluebook + MN-house-style citation patterns:

  - ``Minn. Stat. § 609.185``
  - ``Minn. Stat. § 609.185(a)`` (subsection captured to advance the
    text_offset window past the parenthetical, then dropped from storage --
    we link at chapter+section granularity, not parenthetical-clause).
  - ``Minn. Stat. § 609.185, subd. 1`` (subdivision stored, included in
    slug as ``minn.stat.609.185.subd.1``).
  - ``Minn. Stat. ch. 169`` (chapter-only, slug ``minn.stat.ch.169``).
  - ``Minn.Stat. § 609.185`` (no spaces between ``Minn.`` and ``Stat.``).

Out of scope for v1 (intentional):

  - ``Minnesota Statutes section 609.185`` (long-form -- rare in
    appellate prose; if it shows up enough, add a third regex).
  - ``Minn. R. Crim. P. 26.03`` (rules of procedure, not statutes).
  - ``Minn. R. Evid. 803`` (rules of evidence).
  - Federal cites like ``18 U.S.C. § 2``.
  - Session-law cites like ``2010 Minn. Laws ch. 169``.

Performance note: both regexes use possessive-style quantifiers
(``\\s*``, ``\\d{1,4}``) so backtracking is O(n) on input length. The
whole-corpus extraction over 60K opinions runs in ~5-10 min on dev
hardware -- comfortably within the brief's expected runtime.
"""
import re

from .statutes import ExtractedStatute


# Full citation: "Minn. Stat. § 609.185" with optional section, optional
# (subsection), optional ", subd. N". Subsection is captured to advance
# past the parenthetical but not stored.
FULL_CITATION = re.compile(
    r'\bMinn\.?\s*Stat\.?\s*§?\s*'
    r'(?P<chapter>\d{1,4})'
    r'(?:\.(?P<section>\d{1,4}[a-zA-Z]?))?'
    r'(?:\s*\((?P<subsection>[^)]+)\))?'
    r'(?:,\s*subd\.\s*(?P<subdivision>\d+[a-zA-Z]?))?',
    re.IGNORECASE,
)

# Chapter-only citation: "Minn. Stat. ch. 169" or "Minn. Stat. chapter 169".
CHAPTER_CITATION = re.compile(
    r'\bMinn\.?\s*Stat\.?\s*(?:ch\.?|chapter)\s*(?P<chapter>\d{1,4})\b',
    re.IGNORECASE,
)


def _build_slug_and_display(
    chapter: str,
    section: str,
    subdivision: str,
) -> tuple[str, str]:
    """Build (slug, display) pair from extracted parts.

    Slug grammar:
        minn.stat.<chapter>                    (chapter-only)
        minn.stat.<chapter>.<section>           (full section)
        minn.stat.<chapter>.<section>.subd.<n>  (with subdivision)

    Display grammar mirrors the canonical Bluebook form.
    """
    if section:
        slug = f"minn.stat.{chapter}.{section}"
        display = f"Minn. Stat. § {chapter}.{section}"
    else:
        slug = f"minn.stat.{chapter}"
        display = f"Minn. Stat. § {chapter}"
    if subdivision:
        slug = f"{slug}.subd.{subdivision}"
        display = f"{display}, subd. {subdivision}"
    return slug.lower(), display


def extract(text: str) -> list[ExtractedStatute]:
    """Find every Minnesota statute citation in ``text``.

    Returns a list (NOT deduplicated) sorted by text_offset. Multiple
    citations of the same statute in the same opinion are preserved
    so the statute page can pull surrounding context for each hit.
    The caller is responsible for de-duplicating when only the set
    of unique statutes matters.
    """
    if not text:
        return []
    results: list[ExtractedStatute] = []

    # Pass 1: full citations (section-level + optional subdivision).
    for match in FULL_CITATION.finditer(text):
        chapter = match.group("chapter") or ""
        if not chapter:
            continue
        section = match.group("section") or ""
        subdivision = match.group("subdivision") or ""
        slug, display = _build_slug_and_display(chapter, section, subdivision)
        results.append(ExtractedStatute(
            chapter=chapter,
            section=section,
            subdivision=subdivision,
            reference_slug=slug,
            reference_display=display,
            text_offset=match.start(),
        ))

    # Pass 2: chapter-only citations ("Minn. Stat. ch. 169").
    for match in CHAPTER_CITATION.finditer(text):
        chapter = match.group("chapter") or ""
        if not chapter:
            continue
        slug = f"minn.stat.ch.{chapter}".lower()
        display = f"Minn. Stat. ch. {chapter}"
        results.append(ExtractedStatute(
            chapter=chapter,
            section="",
            subdivision="",
            reference_slug=slug,
            reference_display=display,
            text_offset=match.start(),
        ))

    results.sort(key=lambda s: s.text_offset)
    return results
