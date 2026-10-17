"""Arizona statute citation extractor.

Recognizes Arizona Revised Statutes (A.R.S.) citations in their modern
Bluebook form. Patterns observed across the 2026 AZ Supreme Court
sample set:

  - ``A.R.S. § 13-1103``               (title-section)
  - ``A.R.S. § 13-1103(A)``            (with parenthetical subsection)
  - ``A.R.S. § 13-1103(A)(2)``         (nested parenthetical)
  - ``A.R.S. § 13-751(F)(6)``          (cross-referenced subsection)
  - ``A.R.S. § 12-910(F)``             (administrative-procedure title)
  - ``A.R.S. § 5-104(D)``              (gaming title)

Title is digits; section is digits. The parenthetical that follows is
the subsection ID -- usually a single uppercase letter possibly with
further nested numbering. We store only the FIRST parenthetical capture
as the subdivision, dropping nested levels since the statute page links
at section granularity (legal index, not parenthetical-clause). Multiple
hits of the same section with different subsections collapse onto the
same statute slug.

Slug grammar:
    ars.<title>-<section>                          (title-section only)
    ars.<title>-<section>.<subsection>             (with subsection)

Out of scope for v1:

  - ``A.R.S. §§ 13-4031 and -4033(A)(1)``  (plural-section "and-dash" form;
    rare in practice -- if it shows up enough, add a second-pass regex
    that splits the trailing ", -NNN" continuations against the same title)
  - ``Ariz. Const. art. II, § 4``           (constitutional cites)
  - ``Ariz. R. Crim. P. 13.4``              (rules of criminal procedure)
  - ``Ariz. R. Evid. 401``                  (rules of evidence)
  - ``Ariz. Admin. Code R19-2-124``         (administrative code)
  - ``2015 Ariz. Sess. Laws, ch. 19, § 2``  (session laws)

Reference: https://www.azleg.gov/arsDetail/
"""
import re

from .statutes import ExtractedStatute


# Full citation: "A.R.S. § 13-1103" with optional parenthetical
# subsection like "(A)" or "(F)(6)". We capture only the FIRST
# parenthetical level as the stored subdivision and drop the nested
# remainder -- e.g. "(F)(6)" -> "F". Title and section are digits.
#
# The literal "A.R.S." spelling can vary slightly: "A.R.S." standard,
# "ARS" no-dot occasional in older opinions. Accept the dotted form
# strictly for v1 -- the no-dot form rarely appears in AZ Supreme prose
# and is more likely a false positive (e.g. "ARS" in unrelated acronyms).
FULL_CITATION = re.compile(
    r'\bA\.\s*R\.\s*S\.\s*'
    r'§\s*'
    r'(?P<title>\d{1,3})'
    r'-'
    r'(?P<section>\d{1,5}(?:\.\d{1,4})?)'
    r'(?:\((?P<subdivision>[A-Z0-9]+)\))?',
)

# ---------------------------------------------------------------------------
# v2 grammar, 2026-10-16. MEASURED on 2,998 random AZ opinions: the v1
# pattern above (kept for reference and tests) missed a statute anchor in
# 1,386 of them -- ~3,500 cites against 9,131 found. The misses, by form:
#   A.R.S. §§ 12-341 and 42-18206          1,008   (lists)
#   A.R.S. section 12-2101(B)                645
#   A.R.S. § 42- 18206 / 44 — 1401           591   (pypdf space / dash)
#   AR.S. § 12-1801                          224   (OCR)
#   (“A.R.S.”) section / § / §§             ~300   (defined-term alias)
#   A.R.S. Sec. / Section / A.R.S 13- /
#   Arizona Revised Statutes section ...    ~350
# A second instance of the "rare in appellate prose" trap: the v1
# docstring called the no-section-sign forms rare without measuring.
_ANCHOR = (
    r'(?:\bA\.?\s?R\.?\s?S\b\.?|\[A\.R\.S\.\]|\bArizona\s+Revised\s+Statutes?)'
    r'(?:\s*\(\s*[“"]?A\.R\.S\.[”"]?\s*\))?(?:\s*[”"]?\))?'
)
# A RANGE after the first member ("§§ 13-1401 to -1410", "through
# 13-1410") drops the whole match: storing 13-1401 would invent a cite
# to the range's first member, the rule the MN extractor also keeps.
#
# The endpoint must be STATUTE-SHAPED ("-1410" or "13-1410"): a bare
# "to 5" is prose ("sentenced under A.R.S. § 13-702 to 5 years") and the
# cite before it is real.
_RANGE_AHEAD = re.compile(
    r'\s*(?:(?:to|through)\s+(?:[-–—]\s?|\d{1,3}\s?[-–—]\s?)\d|[-–—]\s?\d)', re.I)
_SINGULAR = r'(?:§|[Ss]ec(?:tion|\.))'
_PLURAL = r'(?:§§|[Ss]ections)'
_DASH = r'\s?[-–—]\s?'
# "(2016)" after a cite is the statute's EDITION YEAR, never a subsection
# -- caught on real text ("-2101 (2016)" stored subsection 2016).
_SUB = r'(?:\s?\((?!(?:19|20)\d{2}\))(?P<subdivision>[A-Z0-9]+)\)(?:\([A-Za-z0-9]+\))*)?'
_NUMBER = (r'(?P<title>\d{1,3})' + _DASH +
           r'(?P<section>[1-9]\d{0,4}(?:\.\d{1,4})?)(?![\d.]\d)' + _SUB)
# Marker is REQUIRED unless the number follows the anchor directly
# ("A.R.S. 17-309(A)(1)"); the volume form "16 A.R.S., Pt. 1" has
# neither and never matches.
V2_CITATION = re.compile(
    _ANCHOR + r'\s*,?\s*(?:(?P<plural>' + _PLURAL + r')|' + _SINGULAR + r')?\s*'
    + _NUMBER
)
# Continuation of a PLURAL cite: ", 13-4033", "and -4033(A)(1)",
# ", 12-120.21(A)(1) (2016) and -2101". A dash-only member inherits the
# previous title. "to" / "through" / a bare dash between members is a
# RANGE and stops the list: a range is never stored as its endpoints.
_YEAR = r'(?:\s?\((?:Supp\.?\s*)?\d{4}(?:\s*Supp\.?)?\))?'
_CONTINUATION = re.compile(
    _YEAR + r'\s*(?:,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)'
    r'(?:(?P<title>\d{1,3})' + _DASH + r'|[-–—]\s?)'
    r'(?P<section>[1-9]\d{0,4}(?:\.\d{1,4})?)(?![\d.]\d)' + _SUB
)


def _build_slug_and_display(
    title: str,
    section: str,
    subdivision: str,
) -> tuple[str, str]:
    """Build (slug, display) pair from extracted parts.

    Slug grammar:
        ars.<title>-<section>                  (title-section only)
        ars.<title>-<section>.<subsection>     (with subsection)

    Display mirrors the canonical Bluebook form.
    """
    slug = f"ars.{title}-{section}"
    display = f"A.R.S. § {title}-{section}"
    if subdivision:
        slug = f"{slug}.{subdivision}"
        display = f"{display}({subdivision})"
    return slug.lower(), display


# A search query that is NOTHING BUT an A.R.S. number: "13-1105",
# "§ 13-1105(A)", "section 28-1381". A section never starts with 0,
# while docket fragments do ("25-0606"), so a leading zero is refused --
# a pasted docket must never be read as a statute. Callers VERIFY each
# candidate against the corpus before redirecting.
BARE_CITE_RE = re.compile(
    r'^\s*(?:A\.\s*R\.\s*S\.\s*)?(?:§\s*|sec(?:tion)?\.?\s*)?'
    r'(?P<body>\d{1,3}-[1-9]\d{0,4}(?:\.\d{1,4})?(?:\s?\([A-Z0-9]+\))?)\s*$',
    re.IGNORECASE,
)


def bare_slug_candidates(query: str) -> list[str]:
    """Slugs a bare A.R.S. query could mean, most specific first.

    Built by re-running ``extract`` over a synthesized canonical cite, so
    routing cannot drift from extraction (the MN construction).
    """
    if not query:
        return []
    shape = BARE_CITE_RE.match(query)
    if shape is None:
        return []
    body = shape.group("body").replace(" ", "")
    body = re.sub(r"\(([a-z0-9]+)\)", lambda m: "(" + m.group(1).upper() + ")", body)
    found = extract("A.R.S. § " + body)
    if not found:
        return []
    cite = found[0]
    candidates = [cite.reference_slug]
    if cite.subdivision:
        candidates.append(_build_slug_and_display(cite.chapter, cite.section, "")[0])
    return candidates


def extract(text: str) -> list[ExtractedStatute]:
    """Find every AZ ARS citation in ``text``.

    Returns a list (NOT deduplicated) sorted by text_offset. Multiple
    citations of the same statute in the same opinion are preserved
    so the statute page can pull surrounding context for each hit.
    The caller is responsible for de-duplicating when only the set
    of unique statutes matters.

    The ExtractedStatute.chapter field carries the A.R.S. *title* for
    AZ -- A.R.S. doesn't use the "chapter" word, but the shared data
    model uses ``chapter`` as the top-level grouping key across states
    so we map title -> chapter to keep the storage uniform.
    """
    if not text:
        return []
    results: list[ExtractedStatute] = []

    def add(title, section, subdivision, offset):
        slug, display = _build_slug_and_display(title, section, subdivision)
        results.append(ExtractedStatute(
            # Map A.R.S. title onto the shared "chapter" column so the
            # storage layer can group by it without an AZ-special path.
            chapter=title,
            section=section,
            subdivision=subdivision,
            reference_slug=slug,
            reference_display=display,
            text_offset=offset,
        ))

    for match in V2_CITATION.finditer(text):
        if _RANGE_AHEAD.match(text, match.end()):
            continue
        title = match.group("title")
        add(title, match.group("section"), match.group("subdivision") or "",
            match.start())
        if not match.group("plural"):
            continue
        # Walk the list. Each member gets its own offset so the statute
        # page can show its own context, and the 'same opinion, slug,
        # offset' key stays unique.
        pos = match.end()
        while True:
            cont = _CONTINUATION.match(text, pos)
            if cont is None or _RANGE_AHEAD.match(text, cont.end()):
                break
            title = cont.group("title") or title
            add(title, cont.group("section"), cont.group("subdivision") or "",
                cont.start("section"))
            pos = cont.end()

    results.sort(key=lambda s: s.text_offset)
    return results
