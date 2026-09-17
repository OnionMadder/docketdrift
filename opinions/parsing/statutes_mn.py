"""Minnesota statute citation extractor.

Recognizes the common Bluebook + MN-house-style citation patterns:

  - ``Minn. Stat. § 609.185``
  - ``Minn. Stat. § 609.185(a)`` (subsection captured to advance the
    text_offset window past the parenthetical, then dropped from storage --
    we link at chapter+section granularity, not parenthetical-clause).
  - ``Minn. Stat. § 609.185, subd. 1`` (subdivision stored, included in
    slug as ``minn.stat.609.185.subd.1``).
  - ``Minn. Stat. § 609.185, subdivision 1`` (subdivision spelled out --
    the MN house style on first reference; same slug as ``subd. 1``).
  - ``Minnesota Statutes section 609.185`` (long form -- also MN house
    style on first reference; same slug as the abbreviated form).
  - ``Minn. Stat. ch. 169`` (chapter-only, slug ``minn.stat.ch.169``).
  - ``Minn.Stat. § 609.185`` (no spaces between ``Minn.`` and ``Stat.``).

The long form and the spelled-out subdivision were BOTH out of scope in
v1, on the stated assumption that they were "rare in appellate prose".
Measured against 1,200 MN opinions (2015+) on 2026-09-16, that assumption
was wrong and the cost was real:

  - ``Minnesota Statutes section N`` appears in **458 of 1,200** opinions
    (1,422 cites), and **47 opinions use it EXCLUSIVELY** -- those cited
    a statute and were entirely invisible to the citation graph.
  - ``, subdivision N`` spelled out appears **1,951** times against 7,208
    for ``, subd. N``. Those matched the section but not the subdivision,
    so a subdivision-specific cite was silently stored at section
    granularity -- a wrong answer, not a missing one.

Lesson worth keeping: "rare" was asserted in a docstring and never
measured. Frequency-rank the real conventions against the real corpus
before scoping an extractor (same method that fixed AZ dispositions and
the MN dissent captions).

Out of scope, still intentional:

  - Citation LISTS (``Minn. Stat. §§ 609.185, .19`` / ``sections 609.185
    and 609.19``). Only the first section of a list is captured. This is
    a pre-existing limit of ``FULL_CITATION`` too, kept deliberately:
    resolving the trailing members needs a grammar that can tell a
    companion section from a year parenthetical, and guessing there
    mints citations the court never made.
  - Plural ``subds. 2-3`` / ``subdivisions 2-3`` (ranges). The singular
    patterns below do not match these, by construction.
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


# Shared tail: optional "(subsection)" then optional ", subd. N".
#
# ``subd\.|subdivision`` is an alternation, NOT ``subd(?:ivision)?\.?``,
# and that is deliberate. The looser form would match the ``subd`` inside
# ``subds. 2-3`` and then take the ``2``, silently converting a RANGE of
# subdivisions into a cite to the first one. With this alternation,
# ``subds.`` fails (no literal ``.`` after ``subd``) and ``subdivisions``
# fails (no digit after the trailing ``s``), so ranges match nothing --
# which is the honest outcome, since we have no way to store a range.
_CITE_TAIL = (
    r'(?:\s*\((?P<subsection>[^)]+)\))?'
    r'(?:\s*,?\s*(?:subd\.|subdivision)\s*(?P<subdivision>\d+[a-zA-Z]?))?'
)
# The leading ``\s*`` before the optional comma is load-bearing: pypdf
# leaves a space before the comma often enough to matter ("Minn. Stat.
# § 563.01 , subd. 3(a)"), and the original ``,\s*subd\.`` missed every
# one of those -- storing a subdivision cite at bare-section granularity.

# Abbreviated citation: "Minn. Stat. § 609.185", "Minn.Stat. 609.185".
FULL_CITATION = re.compile(
    r'\bMinn\.?\s*Stat\.?\s*§?\s*'
    r'(?P<chapter>\d{1,4})'
    r'(?:\.(?P<section>\d{1,4}[a-zA-Z]?))?'
    + _CITE_TAIL,
    re.IGNORECASE,
)

# Long form: "Minnesota Statutes section 609.185, subdivision 1 (2024)",
# "Minnesota Statutes, section 609.185", "Minnesota Statutes § 609.185".
#
# This cannot overlap FULL_CITATION: that pattern needs ``Stat`` to
# follow ``Minn`` (optionally dotted/spaced), and in "Minnesota" the next
# characters are "esota", so it never fires on the long form. The two
# passes are therefore additive, not double-counting -- verified by test.
#
# ``sections?`` accepts the plural that opens a list; only the FIRST
# number is captured (see the module docstring on lists).
LONG_CITATION = re.compile(
    r'\bMinnesota\s+Statutes?\s*,?\s*(?:sections?|§{1,2})\s*'
    r'(?P<chapter>\d{1,4})'
    r'(?:\.(?P<section>\d{1,4}[a-zA-Z]?))?'
    + _CITE_TAIL,
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


# A search query that is NOTHING BUT a section number: "563.01",
# "§ 563.01", "563.01, subd. 3", "section 609.185".
#
# The chapter-section DOT IS REQUIRED. A bare chapter number ("169") is
# too ambiguous to route on -- it is also a page number, a year fragment
# and a dollar figure -- and a wrong redirect is worse than a search.
# The dotted form is precisely the shape InnoDB's tokenizer cannot match
# (it splits at the period and drops any fragment under
# innodb_ft_min_token_size = 3), so this is the query that most needs a
# structured route and can least be served by the text index.
BARE_CITE_RE = re.compile(
    r'^\s*(?P<marker>§\s*|sec(?:tion)?\.?\s*)?'
    r'(?P<body>\d{1,4}\.\d{1,4}[a-zA-Z]?'
    r'(?:\s*,?\s*(?:subd\.|subdivision)\s*\d+[a-zA-Z]?)?)\s*$',
    re.IGNORECASE,
)


def bare_slug_candidates(query: str) -> list[str]:
    """Slugs a bare section-number query could mean, most specific first.

    Returns [] unless the whole query is a section number. The caller is
    expected to VERIFY each candidate against the corpus before
    redirecting -- a candidate is a guess about grammar, not a claim that
    the statute is cited. That check is what makes this safe: a query
    that happens to look like a cite but matches nothing falls through to
    ordinary search instead of landing on a 404.

    Built by re-running ``extract`` over a synthesized canonical cite, so
    the routing grammar cannot drift away from the extraction grammar as
    one or the other is extended.
    """
    if not query:
        return []
    shape = BARE_CITE_RE.match(query)
    if shape is None:
        return []
    # Drop any "§" / "section" the user typed -- we re-add the canonical
    # marker below, and "Minn. Stat. § § 563.01" matches nothing.
    found = extract("Minn. Stat. § " + shape.group("body").strip())
    if not found:
        return []
    cite = found[0]
    candidates = [cite.reference_slug]
    if cite.subdivision:
        # They named a subdivision; fall back to the section page, which
        # now rolls its subdivisions up, if that exact subdivision has
        # never been cited.
        base, _ = _build_slug_and_display(cite.chapter, cite.section, "")
        candidates.append(base)
    return candidates


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

    # Pass 1: abbreviated + long-form citations (section-level + optional
    # subdivision). Both normalize onto the SAME slug, so "Minn. Stat.
    # § 563.01" and "Minnesota Statutes section 563.01" land on one
    # statute page rather than fragmenting the graph by house style.
    for pattern in (FULL_CITATION, LONG_CITATION):
        for match in pattern.finditer(text):
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
