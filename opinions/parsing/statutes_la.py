"""Louisiana statute citation extractor.

Louisiana is a civil-law jurisdiction, so the statutory landscape spans
more source families than the common-law states we already cover. All
carry a ``La.`` prefix; the second token names the code family.

Observed in real samples (2026-CD-00927 Supreme + 5 lactapp COA):

  - ``La. R.S. 13:1335``            (Revised Statutes -- title:section)
  - ``La. R.S. 13:1335(A)``         (with parenthetical subsection)
  - ``La. R.S. 13:1335(A)-(B)``     (subsection range -- first paren only)
  - ``La. R.S. 13:2492 (A)(8)``     (SPACE before paren -- tolerated)
  - ``La. R.S. 40:1231.1``          (decimal in section)
  - ``La. R.S. 40:1231.1, et seq.`` ("et seq." trailer -- ignored)
  - ``La. Ch. C. art. 1138``        (Children's Code)
  - ``La. Const. art. V, § 32``     (Constitution -- article + section)
  - ``La. Const. art. V, § 15(D)``  (with parenthetical subdivision)
  - ``La. Const. art. VII §§ 31``   (plural section marker)
  - ``La. Const. art. V``           (article only, no section)
  - ``La. Const. Art. III, sec. 13`` (rare "sec." variant instead of §)

Extended for build-list coverage:

  - ``La. Civ. Code art. 2315``          (Civil Code, long form)
  - ``La. Code Civ. Proc. art. 425``     (Code of Civil Procedure, long form)
  - ``La. Code Crim. Proc. art. 703``    (Code of Criminal Procedure, long)
  - ``La. Code Evid. art. 803``          (Code of Evidence, long form)

  Plus the LA practitioner shorthands that dominate real citations
  (measured on 10612334 which uses ONLY the short forms):

  - ``La. C.C. art. 2315``               (Civil Code)
  - ``La. C.C.P. art. 1425(F)``          (Code Civ. Proc.)
  - ``La. C.Cr.P. art. 703``             (Code Crim. Proc.)
  - ``La. C.E. arts. 702 through 705``   (Code Evid.; also plural form)

Both ``art.`` and ``arts.`` (plural) are accepted; when a range or
plural is cited (``arts. 702 through 705``, ``arts. 702-705``) only the
FIRST article is captured. Range expansion is a follow-up if the
graph analysis needs it.

Slug grammar (URL-safe, no spaces, lowercase):

    la.rs.<title>-<section>[.<sub>]        (Revised Statutes)
    la.civ.<article>                       (Civil Code)
    la.ccp.<article>                       (Code Civ. Proc.)
    la.crimproc.<article>                  (Code Crim. Proc.)
    la.evid.<article>                      (Code Evid.)
    la.chc.<article>                       (Children's Code)
    la.const.<article>[.<section>]         (Constitution; article
                                            is lowercase roman/arabic)

Out of scope for v1 (deliberate):

  - ``Uniform Rules of Louisiana Courts of Appeal, Rule 4-5(C)`` --
    procedural rules, not statutes. Common in writ dispositions.
  - ``La. R. Prof. Conduct 1.7`` -- Rules of Professional Conduct.
  - ``La. Sup. Ct. R. XIX`` -- Supreme Court disciplinary rules.
  - ``2020 La. Acts, Act 1050`` -- session-law cites (rare).
  - Federal cites (``42 U.S.C. § 1983``) -- these already show up in
    LA opinions but aren't Louisiana statutes.

The § character extracts from PDF as U+00A7 (Latin-1 section sign).
Regexes accept ``§``, ``§§`` (plural), and the rare ``sec.`` variant.
"""
import re

from .statutes import ExtractedStatute


# ---------------------------------------------------------------------------
# La. R.S. (Revised Statutes) -- the dominant citation form.
#
# Title = digits (1-3), section = digits (1-5) with optional decimal.
# Parenthetical subsection is captured OPTIONALLY, with the first paren
# level only (a "(A)-(B)" range or "(A)(8)" nested tail is stored as "A").
# Space tolerated before the paren ("13:2492 (A)(8)").
# ---------------------------------------------------------------------------
RS_CITATION = re.compile(
    r"\bLa\.\s+R\.\s*S\.\s+"
    r"(?P<title>\d{1,3})"
    r":"
    r"(?P<section>\d{1,5}(?:\.\d{1,4})?)"
    r"(?:\s*\((?P<subdivision>[A-Za-z0-9]+)\))?"
)


# ---------------------------------------------------------------------------
# La. Civ. Code / La. Code Civ. Proc. / La. Code Crim. Proc. / La. Code
# Evid. / La. Ch. C.  All share the shape "<Code Name> art[s]. <article>".
#
# Each family carries a LONG form ("La. Code Civ. Proc.") and a SHORT
# practitioner form ("La. C.C.P."). Both appear in real LA opinions;
# some opinions use exclusively the short form. The article follower
# accepts ``art.`` OR ``arts.`` (plural / range); only the FIRST article
# is captured when a range or plural is cited.
#
# One regex per code family keeps the ExtractedStatute chapter distinct
# per family -- that's the key the statute page groups on.
# ---------------------------------------------------------------------------
def _code_pattern(long_form: str, short_form: str) -> re.Pattern:
    """Build a regex matching either the long OR short code-name form."""
    return re.compile(
        r"\bLa\.\s+(?:" + long_form + r"|" + short_form + r")\s+"
        r"arts?\.\s+"
        r"(?P<article>\d{1,5}(?:\.\d{1,4})?)"
        r"(?:\s*\((?P<subdivision>[A-Za-z0-9]+)\))?",
        re.IGNORECASE,
    )


_CODE_FAMILIES = [
    # (family_name, chapter_label, slug_prefix, regex)
    ("Civ. Code", "Civ. Code", "la.civ",
        _code_pattern(r"Civ\.\s+Code", r"C\.\s*C\.")),
    ("Code Civ. Proc.", "Code Civ. Proc.", "la.ccp",
        _code_pattern(r"Code\s+Civ\.\s+Proc\.", r"C\.\s*C\.\s*P\.")),
    ("Code Crim. Proc.", "Code Crim. Proc.", "la.crimproc",
        _code_pattern(r"Code\s+Crim\.\s+Proc\.", r"C\.\s*Cr\.\s*P\.")),
    ("Code Evid.", "Code Evid.", "la.evid",
        _code_pattern(r"Code\s+Evid\.", r"C\.\s*E\.")),
    ("Ch. C.", "Ch. C.", "la.chc",
        _code_pattern(r"Ch\.\s+C\.", r"Ch\.\s*C\.")),
]


# ---------------------------------------------------------------------------
# La. Const. -- constitutional citations. Two shapes:
#   Article + section:  "La. Const. art. V, § 32", "La. Const. art. V, § 15(D)",
#                       "La. Const. Art. III, sec. 13"
#   Article only:       "La. Const. art. V" (rarer; keep for completeness)
#
# The article is Roman (I-XIV) or Arabic. § renders as U+00A7 from PDF;
# also accept "§§" (plural) and the rare "sec." variant.
# ---------------------------------------------------------------------------
CONST_ARTICLE_SECTION = re.compile(
    r"\bLa\.\s+Const\.\s+[Aa]rt(?:icle)?\.?\s+"
    r"(?P<article>[IVXLCM]+|\d+)"
    r"(?:\s*,)?\s+"
    r"(?:§{1,2}|sec\.?)\s*"
    r"(?P<section>\d+)"
    r"(?:\s*\((?P<subdivision>[A-Za-z0-9]+)\))?"
)
CONST_ARTICLE_ONLY = re.compile(
    r"\bLa\.\s+Const\.\s+[Aa]rt(?:icle)?\.?\s+"
    r"(?P<article>[IVXLCM]+|\d+)"
    r"(?!\s*,?\s*(?:§|sec\.))"  # exclude when followed by § / sec.
)


def _rs_slug_and_display(title: str, section: str, sub: str) -> tuple[str, str]:
    slug = f"la.rs.{title}-{section}"
    display = f"La. R.S. {title}:{section}"
    if sub:
        slug = f"{slug}.{sub.lower()}"
        display = f"{display}({sub})"
    return slug, display


def _code_slug_and_display(family: str, prefix: str, article: str,
                           sub: str) -> tuple[str, str]:
    slug = f"{prefix}.{article}"
    display = f"La. {family} art. {article}"
    if sub:
        slug = f"{slug}.{sub.lower()}"
        display = f"{display}({sub})"
    return slug, display


def _const_slug_and_display(article: str, section: str,
                            sub: str) -> tuple[str, str]:
    art_low = article.lower()
    if section:
        slug = f"la.const.art-{art_low}.{section}"
        display = f"La. Const. art. {article}, § {section}"
        if sub:
            slug = f"{slug}.{sub.lower()}"
            display = f"{display}({sub})"
    else:
        slug = f"la.const.art-{art_low}"
        display = f"La. Const. art. {article}"
    return slug, display


def extract(text: str) -> list[ExtractedStatute]:
    """Find every LA statute citation in ``text``.

    Returns a list (NOT deduplicated) sorted by text_offset. Multiple
    citations of the same statute in the same opinion are preserved
    so the statute page can pull surrounding context for each hit.

    The ExtractedStatute.chapter field carries:
      - The R.S. *title* number (e.g. "13") for La. R.S. cites.
      - The code-family name (e.g. "Civ. Code", "Ch. C.") for the
        Code and Children's Code cites.
      - "Const. art. <N>" for constitutional cites (so the chapter
        column groups every article-V citation together for later
        Roman-numeral grouping in the statute-detail page).
    """
    if not text:
        return []
    results: list[ExtractedStatute] = []
    seen_spans: list[tuple[int, int]] = []

    def _add(start: int, end: int, chapter: str, section: str,
             sub: str, slug: str, display: str) -> None:
        # Avoid capturing the same span twice when patterns overlap
        # (e.g. CONST_ARTICLE_ONLY vs CONST_ARTICLE_SECTION on the
        # same article+section run).
        for s, e in seen_spans:
            if start < e and end > s:
                return
        seen_spans.append((start, end))
        results.append(ExtractedStatute(
            chapter=chapter,
            section=section,
            subdivision=sub,
            reference_slug=slug,
            reference_display=display,
            text_offset=start,
        ))

    # --- La. R.S. ---------------------------------------------------
    for m in RS_CITATION.finditer(text):
        title = m.group("title") or ""
        section = m.group("section") or ""
        if not title or not section:
            continue
        sub = m.group("subdivision") or ""
        slug, display = _rs_slug_and_display(title, section, sub)
        _add(m.start(), m.end(), title, section, sub, slug, display)

    # --- La. Civ. Code / Code Civ. Proc. / Code Crim. Proc. / Code
    #     Evid. / Ch. C. --------------------------------------------
    for family, chapter_label, prefix, pat in _CODE_FAMILIES:
        for m in pat.finditer(text):
            article = m.group("article") or ""
            if not article:
                continue
            sub = m.group("subdivision") or ""
            slug, display = _code_slug_and_display(
                family, prefix, article, sub)
            _add(m.start(), m.end(), chapter_label, article, sub,
                 slug, display)

    # --- La. Const. -- section form first (more specific), then
    #     article-only fallback (excluded via negative lookahead).
    for m in CONST_ARTICLE_SECTION.finditer(text):
        article = m.group("article") or ""
        section = m.group("section") or ""
        sub = m.group("subdivision") or ""
        if not article or not section:
            continue
        slug, display = _const_slug_and_display(article, section, sub)
        _add(m.start(), m.end(), f"Const. art. {article}", section, sub,
             slug, display)
    for m in CONST_ARTICLE_ONLY.finditer(text):
        article = m.group("article") or ""
        if not article:
            continue
        slug, display = _const_slug_and_display(article, "", "")
        _add(m.start(), m.end(), "Const.", article, "", slug, display)

    results.sort(key=lambda s: s.text_offset)
    return results
