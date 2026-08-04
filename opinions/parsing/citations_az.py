"""Arizona case-citation extractor for the citation graph.

Arizona could not be built until 2026-08-04, and the reason is worth keeping:
its opinions cite each other mostly by the OFFICIAL reporter ("221 Ariz. 236"),
but ``Opinion.reporter_cite`` held the Pacific cite for almost every opinion
(25,335 Pacific vs 151 official), so the dominant format resolved at 0%. An
extractor written then would have captured ~40% of the real citations while
appearing to succeed. Loading parallel cites fixed the data, not the parser.

Measured on 500 AZ opinions, resolvability against reporter_cite UNION
ParallelCite:

    Ariz. official ("221 Ariz. 236")   3768 hits    0% -> 93%
    Ariz. App.     ("21 Ariz. App. 4")  322 hits    0% -> 98%
    P.2d                               2430 hits   89%
    P.3d                                381 hits   87%

Docket citations are NOT extracted. Arizona dockets are multi-token with a
division prefix ("1 CA-CV 18-0389", "2 CA-CR 2017-0217", "CV-19-0001-PR"), they
appear in captions and in consolidated-appeal lists exactly as Minnesota's do,
and they resolved at only 68% against our docket forms -- which are themselves
inconsistent (6,208 AZ rows still carry a "No. " prefix). The precision cost is
not worth it while the reporter formats resolve in the 90s.
"""
import re

from .citations import ExtractedCitation, _real_boundaries

# Official: "221 Ariz. 236". Must be tried AFTER Ariz. App. so the App. form
# isn't shredded into a bare official cite (see extract()).
ARIZ_OFFICIAL = re.compile(r"\b(?P<vol>\d{1,3})\s+Ariz\.\s+(?P<page>\d{1,4})\b")

# Court of Appeals official: "21 Ariz. App. 4".
ARIZ_APP = re.compile(
    r"\b(?P<vol>\d{1,3})\s+Ariz\.\s?App\.\s+(?P<page>\d{1,4})\b")

# Pacific reporter, both series. The negative lookahead prevents "202 P. 3d
# 1150" being read as first-series volume 202 page 3 -- the same mis-resolution
# trap found in the Minnesota extractor.
PACIFIC = re.compile(
    r"\b(?P<vol>\d{1,4})\s+P\.\s?(?P<series>2d|3d)\s+(?P<page>\d{1,4})\b")

CONTEXT_PAD = 180
_MIN_QUOTE_CHARS = 24
_SENT_BACK = 400
_SENT_FWD = 320

_CITE_CLAUSE = re.compile(
    r"(?:\b(?:see\s+also|see|accord|cf\.|e\.g\.,?|but\s+see|citing|quoting)\s+)?"
    r"(?:"
    r"(?:in\s+re|in\s+the\s+matter\s+of|matter\s+of|state\s+v\.)\s+"
    r"[A-Z][A-Za-z.'&-]+(?:\s+[A-Za-z.'&-]+)*,\s*"
    r"|"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*\s+v\.?\s+"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*,\s*"
    r")?"
    r"(?:\d{1,4}\s+Ariz\.\s?(?:App\.\s?)?\d{1,4}|\d{1,4}\s+P\.\s?[23]d\s+\d{1,4})"
    r"(?:,?\s*(?:¶\s*)?\d{1,4})?"
    r"(?:\s*\((?:App\.|Ariz\.)?[^)]{0,40}\))?"
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


def extract(text: str, self_cite: str = "") -> list[ExtractedCitation]:
    """Every resolvable reference to another Arizona case.

    ``self_cite`` may carry several of the citing opinion's own keys separated
    by ``|``; all are suppressed so an opinion never cites itself.
    """
    if not text:
        return []
    self_keys = {p.strip() for p in (self_cite or "").split("|") if p.strip()}

    found: list[tuple[int, int, str]] = []
    claimed: list[tuple[int, int]] = []

    # Ariz. App. FIRST, and record its span. "21 Ariz. App. 4" also matches the
    # bare official pattern as volume 21 page (nothing) -- worse, "Ariz. App. 4"
    # can look like a page number to the official regex. Claiming the span stops
    # one physical citation being emitted as two different targets.
    for m in ARIZ_APP.finditer(text):
        found.append((m.start(), m.end(),
                      "%s Ariz. App. %s" % (m.group("vol"), m.group("page"))))
        claimed.append((m.start(), m.end()))

    def overlaps(a, b):
        return any(a < ce and cs < b for cs, ce in claimed)

    for m in ARIZ_OFFICIAL.finditer(text):
        if overlaps(m.start(), m.end()):
            continue
        found.append((m.start(), m.end(),
                      "%s Ariz. %s" % (m.group("vol"), m.group("page"))))
    for m in PACIFIC.finditer(text):
        found.append((m.start(), m.end(), "%s P.%s %s" % (
            m.group("vol"), m.group("series"), m.group("page"))))

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
