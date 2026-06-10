"""New Hampshire statute citation extractor.

Recognizes NH Revised Statutes Annotated (RSA) citations in their
modern Bluebook form. Patterns observed across the 2026 NH Supreme
Court sample set:

  - ``RSA 159-B:1``                          (chapter-section)
  - ``RSA 159-B:1 (2023)``                   (with year, year dropped)
  - ``RSA 632-A:2, III (2016)``              (with Roman subdivision)
  - ``RSA 632-A:3, III (Supp. 2024)``        (Supp. year, dropped)
  - ``RSA 126-A:5, VIII``                    (subdivision, no year)
  - ``RSA 631:4, I (2016)``                  (chapter without letter suffix)

The chapter part can carry an optional ``-X`` suffix where X is a
single uppercase letter (159-B, 632-A, 126-A); the section part is
always digits. Subdivisions are Roman numerals.

Slug grammar:
    rsa.<chapter>.<section>                      (chapter-section)
    rsa.<chapter>.<section>.<roman-subdivision>  (with subdivision)

The colon used in the canonical display form is replaced with ``.`` in
the slug to keep URLs free of URL-encoded ``%3A`` noise.

Out of scope for v1:

  - ``N.H. Const. pt. I, art. 16``  (constitutional cites)
  - ``N.H. R. Ev. 401``              (rules of evidence)
  - ``N.H. Admin. R., He-C 203.02``  (administrative rules)

Reference: https://www.gencourt.state.nh.us/rsa/html/indexes/default.html
"""
import re

from .statutes import ExtractedStatute


# Full citation: "RSA 159-B:1" with optional ", <Roman>" subdivision.
# Year parentheticals (e.g. "(2023)" or "(Supp. 2024)") are NOT captured
# because they don't affect the slug -- skip them, the regex stops at the
# section / subdivision boundary.
FULL_CITATION = re.compile(
    r'\bRSA\s+'
    r'(?P<chapter>\d{1,4}(?:-[A-Z])?)'
    r':(?P<section>\d{1,4}[a-zA-Z]?)'
    r'(?:,\s*(?P<subdivision>[IVXLCDM]+))?',
)


def _build_slug_and_display(
    chapter: str,
    section: str,
    subdivision: str,
) -> tuple[str, str]:
    """Build (slug, display) pair from extracted parts.

    Slug grammar:
        rsa.<chapter>.<section>                         (chapter-section)
        rsa.<chapter>.<section>.<roman-subdivision>     (with subdivision)

    Display mirrors the canonical Bluebook form.
    """
    slug = f"rsa.{chapter}.{section}"
    display = f"RSA {chapter}:{section}"
    if subdivision:
        slug = f"{slug}.{subdivision}"
        display = f"{display}, {subdivision}"
    return slug.lower(), display


def extract(text: str) -> list[ExtractedStatute]:
    """Find every NH RSA citation in ``text``.

    Returns a list (NOT deduplicated) sorted by text_offset. Multiple
    citations of the same statute in the same opinion are preserved
    so the statute page can pull surrounding context for each hit.
    The caller is responsible for de-duplicating when only the set
    of unique statutes matters.
    """
    if not text:
        return []
    results: list[ExtractedStatute] = []

    for match in FULL_CITATION.finditer(text):
        chapter = match.group("chapter") or ""
        section = match.group("section") or ""
        if not chapter or not section:
            continue
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

    results.sort(key=lambda s: s.text_offset)
    return results
