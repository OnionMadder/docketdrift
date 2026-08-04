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

1. **Minnesota's official reporter is now IN scope** (changed 2026-08-04).
   It was excluded at 3% resolvable, because ``Opinion.reporter_cite`` held
   only the regional N.W. cite. Loading parallel cites from CourtListener's
   bulk export took ``123 Minn. 456`` from **3% -> 94%**, so the reason for
   excluding it no longer holds. It was the second most common format by hits
   all along, so the earlier sweep was missing a whole class of real edges.

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

# Minnesota's OFFICIAL reporter: "123 Minn. 456". Excluded from v1 because it
# resolved at 3% -- reporter_cite held only the regional N.W. cite. After the
# parallel-cite load (2026-08-04) it resolves at 94%, so it is in scope. Guard
# against "Minn. App." / "Minn. Stat." by requiring a page number immediately
# after "Minn.", which a statute cite ("Minn. Stat. 609.185") never has.
MINN_OFFICIAL = re.compile(r"\b(?P<vol>\d{1,3})\s+Minn\.\s+(?P<page>\d{1,4})\b")

# Docket: "A19-1234", also "A19-234" in older text (padded on normalize).
DOCKET = re.compile(r"\bA(?P<yy>\d{2})-(?P<seq>\d{3,4})\b")

# What must FOLLOW a docket for it to count as a citation rather than a
# caption entry: an optional pinpoint/parallel run, then a Minnesota court
# parenthetical -- "(Minn. App. Mar. 2, 2020)", "(Minn. 2019)",
# "(Minn. App. filed Mar. 2, 2020)". Anchored with .match() at the docket's
# end so it can only look forward a bounded distance.
_DOCKET_CITE_TAIL = re.compile(
    r"[^()\n]{0,80}?\(\s*Minn\.(?:\s*(?:Ct\.\s*)?App\.)?[^)]{0,60}\)",
    re.I,
)

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
    # Canonicalize the self keys rather than trusting how they're stored.
    # ~15K rows still carry a malformed docket ('a230373', 'NO. A15-1285',
    # 'A15-178'); uppercasing 'a230373' yields 'A230373', which never matches
    # the canonical 'A23-0373' this extractor emits -- so the opinion cites
    # ITSELF via its own caption. Measured, not hypothetical.
    self_keys = set()
    for part in re.split(r"[|]", self_cite or ""):
        p = part.strip()
        if not p:
            continue
        self_keys.add(p)
        bare = p.upper().replace("NO.", "").replace(" ", "")
        m = re.match(r"^A(\d{2})-?(\d{1,4})$", bare)
        if m:
            self_keys.add(normalize_docket(m.group(1), m.group(2)))

    found: list[tuple[int, int, str]] = []
    for m in NW2D.finditer(text):
        found.append((m.start(), m.end(),
                      "%s N.W.2d %s" % (m.group("vol"), m.group("page"))))
    for m in NW1.finditer(text):
        found.append((m.start(), m.end(),
                      "%s N.W. %s" % (m.group("vol"), m.group("page"))))
    for m in MINN_OFFICIAL.finditer(text):
        found.append((m.start(), m.end(),
                      "%s Minn. %s" % (m.group("vol"), m.group("page"))))
    for m in DOCKET.finditer(text):
        # A bare docket number is NOT a citation. Every MN opinion prints its
        # own in the caption, and a CONSOLIDATED appeal prints its companions'
        # there too ("A23-0373, A23-0621") -- extracting those invents edges
        # between documents that are one proceeding, not a citing pair.
        # A real citation to an unpublished case carries a court-and-date
        # parenthetical: "State v. Doe, No. A19-1234 (Minn. App. Mar. 2, 2020)".
        # Require it. Costs some recall, buys precision -- the right trade for
        # a record that is supposed to be verifiable.
        if not _DOCKET_CITE_TAIL.match(text, m.end()):
            continue
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
