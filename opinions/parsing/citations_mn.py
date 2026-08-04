"""Minnesota case-citation extractor for the citation graph.

Measured against a 900-opinion random sample of the MN corpus (2026-08-04),
counting both how often each format appears AND how much of it resolves to an
opinion we actually hold -- an unresolvable cite is noise, not an edge:

    format          hits   docs   resolvable
    N.W.2d          3726    427      94%      <- primary
    N.W. (1st)      1575    346      88%      <- same family, older opinions
    docket A##-####  165    130      87%      <- MN-only path, see below
    Minn. official  2605    562       3%      <- EXCLUDED, see below
    WL / U.S.        685      -      n/a      <- out of corpus

Three deliberate scope decisions:

1. **Minnesota's own official reporter is EXCLUDED**, despite being the second
   most common format by hits. ``Opinion.reporter_cite`` holds the regional
   N.W. cite, not the official ``123 Minn. 456``, so only 3% resolve. Emitting
   them would add ~2,500 unresolvable references per 900 opinions. If parallel
   official cites are ever loaded, this becomes a one-line addition.

2. **Docket citations ARE extracted** -- something the NH extractor has no
   equivalent for. Minnesota opinions cite unpublished decisions by docket
   ("State v. Doe, No. A19-1234 (Minn. App. ...)"), and a docket is the ONLY
   key that reaches an opinion with no reporter cite. That includes every
   unpublished opinion and all 3,102 opinions backfilled for 2020-2022, which
   CourtListener has no data for and therefore can never supply edges for.

3. **A self-docket guard is mandatory.** Every MN opinion prints its own case
   number in the caption, so without excluding it, each opinion would cite
   itself -- and for the 1,292 dockets carrying both a COA and a Supreme
   opinion, would draw a spurious edge to its own sibling.
"""
import re

from .citations import ExtractedCitation, _real_boundaries

# "425 N.W.2d 580" / "425 N. W. 2d 580". Tolerates the spacing that PDF text
# extraction injects between the reporter's letters.
NW2D = re.compile(r"\b(?P<vol>\d{1,4})\s+N\.\s?W\.\s?2d\s+(?P<page>\d{1,4})\b")

# First series: "131 N.W. 855". The negative lookahead keeps it from eating a
# spaced second-series cite ("131 N. W. 2d 855") -- without it, that parses as
# volume 131, page 2, and silently resolves to the wrong case.
NW1 = re.compile(
    r"\b(?P<vol>\d{1,4})\s+N\.\s?W\.\s+(?!2d\b)(?P<page>\d{1,4})\b")

# Docket: "A19-1234", also "A19-234" in older text (padded on normalize).
DOCKET = re.compile(r"\bA(?P<yy>\d{2})-(?P<seq>\d{3,4})\b")

CONTEXT_PAD = 180
_MIN_QUOTE_CHARS = 24
_SENT_BACK = 400
_SENT_FWD = 320

# A full MN citation clause, stripped from the display quote so the reader
# sees the proposition rather than the citation apparatus. Covers an optional
# introductory signal, an optional case name, the cite itself, and an optional
# court/date parenthetical -- "(Minn. 1988)", "(Minn. App. Nov. 22, 2021)".
_CITE_CLAUSE = re.compile(
    r"(?:\b(?:see\s+also|see|accord|cf\.|e\.g\.,?|but\s+see|citing|quoting)\s+)?"
    r"(?:"
    r"(?:in\s+re|in\s+the\s+matter\s+of|matter\s+of|petition\s+of|"
    r"appeal\s+of|estate\s+of|state\s+v\.)\s+[A-Z][A-Za-z.'&-]+"
    r"(?:\s+[A-Za-z.'&-]+)*,\s*"
    r"|"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*\s+v\.?\s+"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*,\s*"
    r")?"
    r"(?:No\.\s*)?"
    r"(?:\d{1,4}\s+N\.\s?W\.\s?(?:2d\s?)?\d{1,4}|A\d{2}-\d{3,4})"
    r"(?:,?\s*\d{1,4})?"                       # pinpoint page
    r"(?:\s*\((?:Minn\.[^)]*|[^)]{0,40})\))?"  # (Minn. App. 2021)
    r"[.,;]?",
    re.I,
)


def normalize_docket(yy: str, seq: str) -> str:
    """Canonical MN docket form: A19-1234 (sequence zero-padded to 4)."""
    return "A%s-%s" % (yy, seq.zfill(4))


def _tidy(s: str) -> str:
    return " ".join((s or "").split()).strip(" ;,")


def _substantive(s: str) -> bool:
    """Is what's left after stripping the citation actually a proposition?"""
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
    """Sentence around the cite, with the citation clause removed."""
    s, e = _sent_start(text, start), _sent_end(text, end)
    sentence = text[s:e]
    if not _substantive(sentence):
        return ""
    return _tidy(_CITE_CLAUSE.sub("", sentence))


def extract(text: str, self_cite: str = "") -> list[ExtractedCitation]:
    """Every resolvable reference to another Minnesota case.

    ``self_cite`` may carry the citing opinion's own reporter cite AND its own
    docket number, whitespace- or pipe-separated; both are suppressed. Callers
    that omit the docket will draw a self-edge from the caption.
    """
    if not text:
        return []
    self_keys = {p.strip() for p in re.split(r"[|]", self_cite or "") if p.strip()}

    found: list[tuple[int, int, str]] = []
    for m in NW2D.finditer(text):
        found.append((m.start(), m.end(),
                      "%s N.W.2d %s" % (m.group("vol"), m.group("page"))))
    for m in NW1.finditer(text):
        found.append((m.start(), m.end(),
                      "%s N.W. %s" % (m.group("vol"), m.group("page"))))
    for m in DOCKET.finditer(text):
        found.append((m.start(), m.end(),
                      normalize_docket(m.group("yy"), m.group("seq"))))

    results: list[ExtractedCitation] = []
    seen: set[str] = set()
    for start, end, key in sorted(found):
        if key in self_keys or key in seen:
            continue
        seen.add(key)
        results.append(ExtractedCitation(
            reporter_cite=key,
            text_offset=start,
            context=" ".join(
                text[max(0, start - CONTEXT_PAD):end + CONTEXT_PAD].split()),
            quote=_display_quote(text, start, end),
        ))
    return results
