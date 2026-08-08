"""Louisiana case-citation extractor for the citation graph.

Louisiana appellate opinions cite each other via three reporter families:

  - **Southern Reporter (So., So. 2d, So. 3d)** -- dominant modern form.
    ``902 So. 2d 373`` (with space) OR ``902 So.2d 373`` (no space);
    both variants appear in real opinions and both must resolve to the
    same canonical form.
  - **Louisiana Annuals (La. Ann.)** -- 1846-1900 archive era.
    ``35 La. Ann. 1141``
  - **Louisiana Reports (La., pre-1972)** -- old official reporter.
    ``211 La. 236``. Careful: ``La.`` is also the state abbreviation
    inside court parentheticals (``(La. 1994)``), so bare-``La.`` needs
    the volume-before AND page-after context to disambiguate.

Louisiana uses a MEDIUM-NEUTRAL primary citation format
(``2023-CA-1234, p. 5 (La. App. 4 Cir. 5/16/23), 500 So.3d 123``),
where the So.<n>d cite is the parallel. For v1 we extract the parallel
Southern Reporter cite -- that's what ``Opinion.reporter_cite`` /
``ParallelCite`` will hold once Phase 7 loads the CL bulk reporter map.
Docket-based citation extraction is DEFERRED (LA has three per-circuit
docket formats; scope creep for v1).

The parenthetical carries the citing court's identity + date:

  - ``(La. YYYY)`` / ``(La. M/D/YY)`` -- Supreme
  - ``(La. App. <N> Cir. YYYY)`` / ``(La. App. <N> Cir. M/D/YY)`` -- COA
    (the ``<N>`` circuit number is a strong data signal for
    ``assign_la_circuits`` -- exposed via the ExtractedCitation quote
    for later mining, not captured as its own field in v1)

Federal / other-state parallels that appear in the same opinion prose
and MUST NOT be extracted as LA cites (would inflate false edges):

  - ``508 U.S. 10`` -- US Supreme
  - ``113 S. Ct. 2786`` -- US Supreme Court Reporter
  - ``125 L. Ed. 2d 469`` -- US Lawyer's Edition
  - ``500 F.2d 100`` / ``500 F.3d 100`` -- federal circuit
  - Other regional reporters (A.2d, N.W.2d, N.E.2d, P.2d, S.W.2d, etc.)

The So. family is jurisdictionally shared across LA/AL/MS/FL, but
volume+page IS globally unique in the Southern Reporter series, so an
``800 So.2d 100`` cite resolves unambiguously to one case regardless
of which state authored it. Cites to AL/MS/FL cases in an LA opinion
simply won't find a matching Opinion in our LA-only corpus -- no false
edge results. Correct behavior falls out of the shape.

Measured on the 6 sample set: ~20+ So.<n>d hits across the 5 lactapp
+ Supreme opinions, plus 1 La. Ann. cite in the Supreme. Resolution
will be 0% until Phase 7 loads LA parallel cites -- expected and
disclosed. Do NOT interpret 0% resolution as an extractor failure.
"""
import re

from .citations import ExtractedCitation, _real_boundaries


# ---------------------------------------------------------------------------
# Reporter patterns
# ---------------------------------------------------------------------------
# Southern Reporter (So., So. 2d, So. 3d). Both spacing variants:
#   "902 So. 2d 373"  (space between So. and 2d)
#   "902 So.2d 373"   (no space; common in modern LA prose)
# Series is empty (So.) or "2d"/"3d". Volume 1-4 digits, page 1-5 digits.
SOUTHERN = re.compile(
    r"\b(?P<vol>\d{1,4})\s+So\.\s?(?P<series>[23]d)?\s+(?P<page>\d{1,5})\b"
)

# La. Ann. (Louisiana Annuals; 1846-1900). Distinct from bare "La." because
# of the "Ann." token, so no disambiguation needed.
LA_ANN = re.compile(
    r"\b(?P<vol>\d{1,3})\s+La\.\s+Ann\.\s+(?P<page>\d{1,4})\b"
)

# La. (Louisiana Reports; pre-1972 official). This is the ambiguity trap:
# "La." appears in every court parenthetical ("(La. 1994)"), so a bare
# "La." match must be flanked by <vol>-before AND <page>-after with NO
# intervening "Ann." (otherwise La. Ann. would double-match here).
# Negative lookbehind: not preceded by "(" (rules out parentheticals).
# Negative lookahead: not followed by "Ann." (rules out La. Ann.).
LA_OFFICIAL = re.compile(
    r"(?<!\()\b(?P<vol>\d{1,3})\s+La\.\s+(?!Ann\.)(?P<page>\d{1,4})\b"
)

# Court parenthetical: "(La. App. N Cir. ...)". Exposed for downstream
# assign_la_circuits mining -- the extractor doesn't build a separate
# circuit field in v1 but the context/quote windows preserve it.
# Also handles OCR variants like "Ist Cir." -> "1st Cir." (seen in samples).
COURT_PAREN = re.compile(
    r"\(La\.\s+(?:App\.\s+)?[^)]{0,50}\)"
)


# ---------------------------------------------------------------------------
# Quote / context window helpers -- same shape as citations_az.py
# ---------------------------------------------------------------------------
CONTEXT_PAD = 180
_MIN_QUOTE_CHARS = 24
_SENT_BACK = 400
_SENT_FWD = 320

# Regex used to strip citation clauses out of a display quote so what remains
# is the court's substantive language. Mirrors AZ's shape, with LA-specific
# case-name conventions ("State v. X" is dominant; "State ex rel. X").
_CITE_CLAUSE = re.compile(
    r"(?:\b(?:see\s+also|see|accord|cf\.|e\.g\.,?|but\s+see|citing|quoting)\s+)?"
    r"(?:"
    r"(?:in\s+re|in\s+the\s+matter\s+of|matter\s+of|"
    r"state\s+(?:ex\s+rel\.\s+\S+\s+)?v\.|succession\s+of)\s+"
    r"[A-Z][A-Za-z.'&-]+(?:\s+[A-Za-z.'&-]+)*,\s*"
    r"|"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*\s+v\.?\s+"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*,\s*"
    r")?"
    # Any of: docket-and-date prefix, So.<n>d, La. Ann., La. (official)
    r"(?:"
    r"\d{4}-[A-Z]{1,3}-\d{3,5},?\s*(?:p\.\s*\d{1,4},?\s*)?"
    r"|"
    r"\d{1,4}\s+So\.\s?[23]?d?\s+\d{1,5}"
    r"|"
    r"\d{1,4}\s+La\.\s+(?:Ann\.\s+)?\d{1,4}"
    r")"
    r"(?:,?\s*\d{1,4})?"                        # pinpoint page
    r"(?:\s*\(La\.[^)]{0,60}\))?"               # court parenthetical
    r"[.,;]?",
    re.I,
)


def _tidy(s: str) -> str:
    return " ".join((s or "").split()).strip(" ;,")


def _substantive(s: str) -> bool:
    body = _CITE_CLAUSE.sub("", s)
    body = re.sub(r"\b(?:see\s+also|see|accord|cf\.|e\.g\.|but\s+see|id\.)\b",
                  "", body, flags=re.I)
    return len(_tidy(body)) >= _MIN_QUOTE_CHARS


def _sent_start(text: str, pos: int) -> int:
    window = text[max(0, pos - _SENT_BACK):pos]
    bounds = _real_boundaries(window)
    return max(0, pos - _SENT_BACK) + (bounds[-1] if bounds else 0)


def _sent_end(text: str, pos: int) -> int:
    window = text[pos:pos + _SENT_FWD]
    bounds = _real_boundaries(window)
    return pos + (bounds[0] if bounds else len(window))


def _display_quote(text: str, start: int, end: int) -> str:
    s, e = _sent_start(text, start), _sent_end(text, end)
    sentence = text[s:e]
    if not _substantive(sentence):
        return ""
    return _tidy(_CITE_CLAUSE.sub("", sentence))


def _normalize_southern(vol: str, series: str | None, page: str) -> str:
    """Canonical Southern Reporter form: '902 So. 2d 373' (space).

    Both real spacings appear in prose ('So. 2d' with space, 'So.2d'
    without). Normalizing to the spaced form on extraction matches what
    CL bulk stores in reporter_cite / ParallelCite for LA opinions, so
    the graph resolves against a single canonical shape.
    """
    if series:
        return f"{vol} So. {series} {page}"
    return f"{vol} So. {page}"


def extract(text: str, self_cite: str = "") -> list[ExtractedCitation]:
    """Every resolvable reference to another Louisiana case.

    ``self_cite`` may carry several of the citing opinion's own keys
    separated by ``|``; all are suppressed so an opinion never cites
    itself.

    Returns a list sorted by text offset, ONE entry per unique
    reporter cite (first-occurrence position preserved). Downstream
    resolution (extract_citations command) matches against
    ``Opinion.reporter_cite`` UNION ``ParallelCite.cite``; expect
    ~0% resolution until Phase 7 loads LA parallel cites, then a
    steady climb as coverage fills in.
    """
    if not text:
        return []
    self_keys = {p.strip() for p in (self_cite or "").split("|") if p.strip()}

    found: list[tuple[int, int, str]] = []
    claimed: list[tuple[int, int]] = []

    # La. Ann. FIRST -- its "La." would otherwise be shadowed by the bare
    # LA_OFFICIAL regex if we ran that first (the negative lookahead
    # protects against that specifically, but claiming the span here is
    # belt-and-braces).
    for m in LA_ANN.finditer(text):
        found.append((m.start(), m.end(),
                      f"{m.group('vol')} La. Ann. {m.group('page')}"))
        claimed.append((m.start(), m.end()))

    def overlaps(a: int, b: int) -> bool:
        return any(a < ce and cs < b for cs, ce in claimed)

    for m in LA_OFFICIAL.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        found.append((m.start(), m.end(),
                      f"{m.group('vol')} La. {m.group('page')}"))
    for m in SOUTHERN.finditer(text):
        cite = _normalize_southern(
            m.group("vol"), m.group("series"), m.group("page"))
        found.append((m.start(), m.end(), cite))

    results: list[ExtractedCitation] = []
    seen: set[str] = set()
    for start, end, cite in sorted(found):
        if cite in self_keys or cite in seen:
            continue
        seen.add(cite)
        results.append(ExtractedCitation(
            reporter_cite=cite,
            text_offset=start,
            context=" ".join(
                text[max(0, start - CONTEXT_PAD):end + CONTEXT_PAD].split()),
            quote=_display_quote(text, start, end),
        ))
    return results
