r"""Minnesota statute citation extractor.

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
  - ``Minn. Stat. § 518B.01`` (LETTERED CHAPTER -- see below).

LETTERED CHAPTERS WERE SILENTLY MISFILED UNTIL 2026-09-20, and this was
a wrong answer rather than a missing one. ``(?P<chapter>\d{1,4})``
stopped at the digits, so ``518B.01`` matched chapter ``518``; the
section group needs a literal ``.`` next, saw ``B``, and gave up. The
cite was stored as ``minn.stat.518`` -- Marriage Dissolution -- when the
court had cited 518B, the Domestic Abuse Act. Every Order for Protection
case in the corpus landed on the wrong chapter's page, and 518B had no
page at all.

Measured before the fix: **237 of 900 recent MN opinions (26%)** carry at
least one lettered-chapter cite, and ZERO stored slugs had a letter. The
affected chapters are the core of self-represented litigation -- 260C
juvenile protection, 253B civil commitment, 169A DWI, 504B
landlord-tenant, 518A child support, 609A expungement, 245C background
studies.

Found the way the NH 71-edge bug was found: a number too small to be
plausible. ``minn.stat.518b.01`` returned exactly zero in a corpus that
obviously litigates OFPs.

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
    r'(?P<chapter>\d{1,4}[A-Z]?)'
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
    r'(?P<chapter>\d{1,4}[A-Z]?)'
    r'(?:\.(?P<section>\d{1,4}[a-zA-Z]?))?'
    + _CITE_TAIL,
    re.IGNORECASE,
)

# Chapter-only citation: "Minn. Stat. ch. 169" or "Minn. Stat. chapter 169".
CHAPTER_CITATION = re.compile(
    r'\bMinn\.?\s*Stat\.?\s*(?:ch\.?|chapter)\s*(?P<chapter>\d{1,4}[A-Z]?)\b',
    re.IGNORECASE,
)


# Every unpublished Minnesota opinion opens by reciting the statute that
# restricts citing it: "This opinion will be unpublished and may not be
# cited except as provided by Minn. Stat. Sec. 480A.08, subd. 3 (2024)."
#
# Counted plainly that made 480A.08 the MOST-CITED STATUTE IN MINNESOTA
# at 7,436 cites -- 3.2x section 645.16, the statutory-construction
# canons, and it is not a statute anyone argued. It is the same trap the
# rule layer already handles for Minn. R. Civ. App. P. 136.01, subd.
# 1(c), and the same trap as "accordingly, we" in the holdings
# extractor: frequency is not significance.
#
# Measured on prod 2026-09-21, both directions:
#
#   * 96.6% of its 7,436 occurrences sit in the first 400 characters.
#     Ranking every MN slug that way, the next entry is 73.8% and the
#     one after that 36.2% -- a clean gap, so this is one statute and
#     not a class. AZ, NH and LA top out at 55.8% and have no
#     equivalent; the field stays False there on evidence.
#   * On a 400-occurrence random sample this cue fires on 95.5%, and
#     NONE of the 18 misses sit in the head. Every miss read by hand is
#     a GENUINE cite: the court citing 480A.08 for the proposition that
#     unpublished opinions are not precedential, deep in the discussion.
#
# That is why the cue is narrow. Widening it to "unpublished" or "not
# precedential" would flag all 18 of those real citations, which is the
# 136.01 lesson restated -- flag PER OCCURRENCE on text evidence, keep
# the row, and never blanket-exclude by number.
_BOILERPLATE_CUE = re.compile(
    r"nonprecedential|not\s+be\s+cited|will\s+be\s+unpublished", re.I)

# (chapter, section, subdivision) of the citation-restriction statute.
_BOILERPLATE_CITE = ("480A", "08", "3")

# The disclaimer sentence runs ahead of the cite, so the cue lives
# behind it. 260 chars is the rule layer's window, kept identical
# because the sentence is the same length in both places.
_BOILERPLATE_WINDOW = 260


def _is_boilerplate(text: str, start: int, chapter: str, section: str,
                    subdivision: str) -> bool:
    """True when this occurrence is the nonprecedential-opinion notice.

    Gated on the specific statute AND on text evidence in front of it,
    never on the number alone -- 480A.08 has genuine citations and they
    must keep counting.
    """
    if (chapter.upper(), section, subdivision) != _BOILERPLATE_CITE:
        return False
    window = text[max(0, start - _BOILERPLATE_WINDOW):start]
    return bool(_BOILERPLATE_CUE.search(window))


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
    # Minnesota chapters carry a LETTER SUFFIX often enough to matter:
    # 518B (Domestic Abuse Act), 260C (juvenile protection), 253B (civil
    # commitment), 169A (DWI), 504B (landlord-tenant), 518A (child
    # support), 609A (expungement). The suffix is part of the chapter's
    # IDENTITY, not decoration -- chapter 518 is Marriage Dissolution and
    # 518B is the Domestic Abuse Act, different bodies of law.
    #
    # Display uppercases it because that is how the legislature and the
    # courts write it; the slug lowercases, like every other slug here,
    # so URLs stay case-stable.
    chapter = chapter.upper()
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
    r'(?P<body>\d{1,4}[A-Z]?\.\d{1,4}[a-zA-Z]?'
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
                is_boilerplate=_is_boilerplate(
                    text, match.start(), chapter, section, subdivision),
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
