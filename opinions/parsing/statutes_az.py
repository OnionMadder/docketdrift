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

    for match in FULL_CITATION.finditer(text):
        title = match.group("title") or ""
        section = match.group("section") or ""
        if not title or not section:
            continue
        subdivision = match.group("subdivision") or ""
        slug, display = _build_slug_and_display(title, section, subdivision)
        results.append(ExtractedStatute(
            # Map A.R.S. title onto the shared "chapter" column so the
            # storage layer can group by it without an AZ-special path.
            chapter=title,
            section=section,
            subdivision=subdivision,
            reference_slug=slug,
            reference_display=display,
            text_offset=match.start(),
        ))

    results.sort(key=lambda s: s.text_offset)
    return results
