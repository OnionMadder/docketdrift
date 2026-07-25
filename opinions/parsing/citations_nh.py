"""New Hampshire case-citation extractor for the citation graph.

NH adopted neutral citations effective January 1, 2024: opinions cite each
other as ``<Case Name>, <year> N.H. <n>`` (e.g. "State v. Romero, 2026 N.H.
7"). We extract the neutral cite plus a surrounding context window. The cite
normalizes to the exact ``<year> N.H. <n>`` form stored in
``Opinion.reporter_cite``, so internal edges resolve by exact match.

Out of scope for v1 (don't resolve to our corpus, add false-positive noise):
  - pre-2024 NH regional cites ("123 N.H. 456")
  - regional/federal reporters ("900 A.2d 12", "410 U.S. 113", "88 F.3d 1")
These can be added once we backfill the corresponding reporter cites.
"""
import re

from .citations import ExtractedCitation, _real_boundaries

# Neutral cite: "<year> N.H. <n>", year 2024+ (the neutral-citation era).
# A 4-digit year >= 2024 immediately before "N.H." is unambiguously a neutral
# reporter cite, not prose, so false positives are negligible.
NEUTRAL_CITE = re.compile(r"\b(?P<year>20\d{2})\s+N\.H\.\s+(?P<num>\d{1,4})\b")

# Context window (chars each side of the cite) kept for treatment cues + UI.
CONTEXT_PAD = 180

# A full NH neutral-cite CLAUSE, stripped out of the display quote so it reads
# as the bare proposition (Scholar-style). Matches an optional introductory
# signal, an optional "X v. Y," antecedent case name, the neutral cite, and an
# optional ", ¶ 12" / "para 8" / parallel-reporter pinpoint.
_CITE_CLAUSE = re.compile(
    r"(?:\b(?:see\s+also|see|accord|cf\.|e\.g\.,?|but\s+see)\s+)?"
    r"(?:"
    # "In re X", "Matter of X", "Petition of X", "Appeal of X", "Estate of X",
    r"(?:in\s+re|in\s+the\s+matter\s+of|matter\s+of|petition\s+of|"
    r"appeal\s+of|estate\s+of)\s+[A-Z][A-Za-z.'&-]+(?:\s+[A-Za-z.'&-]+)*,\s*"
    r"|"
    # "X v. Y" party-vs-party.
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*\s+v\.?\s+"
    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*,\s*"
    r")?"
    r"20\d{2}\s+N\.H\.\s+\d{1,4}"
    r"(?:,\s*(?:¶+|paras?\.?|pp?\.)\s*\d+(?:[-,]\s*\d+)*)?"
    r"(?:,\s*\d+\s+[A-Z][\w.]+\s+\d+)?",
    re.IGNORECASE,
)

# Minimum length for a candidate quote to be worth showing.
_MIN_QUOTE_CHARS = 24
# How far to look for the start of the cite's sentence (and the one before it).
_SENT_BACK = 400
_SENT_FWD = 320


def _tidy(s: str) -> str:
    """Clean up the artifacts left after stripping a citation clause."""
    s = re.sub(r"\(\s*[;,]?\s*\)", "", s)        # empty/leftover parens
    s = re.sub(r"(?:,\s*){2,}", ", ", s)           # collapse repeated commas
    s = re.sub(r"\s+([,.;:)])", r"\1", s)          # space-before-punct
    s = re.sub(r"\(\s+", "(", s)                    # space-after-open-paren
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = s.strip(" ,;:")
    # drop a dangling leading conjunction/signal left by the strip
    s = re.sub(r"^(?:and|but|see\s+also|see|accord|cf\.|e\.g\.,?)\s+",
               "", s, flags=re.IGNORECASE)
    return s.strip()


def _substantive(s: str) -> bool:
    """True when ``s`` is real prose, not a citation stub like 'Doe,' or 'para 8.'."""
    return len(s) >= _MIN_QUOTE_CHARS and len(re.findall(r"[A-Za-z]{2,}", s)) >= 4


def _sent_start(text: str, pos: int) -> int:
    """Char index of the start of the sentence containing ``pos``."""
    lo = max(0, pos - _SENT_BACK)
    bounds = _real_boundaries(text[lo:pos])
    return lo + bounds[-1] if bounds else lo


def _sent_end(text: str, pos: int) -> int:
    """Char index just past the end of the sentence containing ``pos``."""
    hi = min(len(text), pos + _SENT_FWD)
    bounds = _real_boundaries(text[pos:hi])
    return pos + bounds[0] if bounds else hi


def _display_quote(text: str, start: int, end: int) -> str:
    """Lift the proposition the citation supports, citation-stripped.

    In legal prose the cited proposition sits EITHER in the sentence the cite
    is embedded in ("The court in X, 2026 N.H. 7, held ...") OR in the
    sentence just before a standalone citation sentence ("Dismissal is proper
    .... See X, 2026 N.H. 7."). We try the cite's own sentence first; if
    stripping the cite leaves only a stub, we fall back to the preceding
    sentence -- which is the proposition the citation backs.
    """
    if not text:
        return ""
    s = _sent_start(text, start)
    e = _sent_end(text, end)
    cite_sent = " ".join(text[s:e].split())
    cand = _tidy(_CITE_CLAUSE.sub("", cite_sent))
    if _substantive(cand):
        return cand
    # Standalone citation sentence -> the proposition is the sentence before it.
    if s > 0:
        ps = _sent_start(text, s - 1)
        prev = _tidy(_CITE_CLAUSE.sub("", " ".join(text[ps:s].split())))
        if _substantive(prev):
            return prev
    # Couldn't lift a clean proposition -> no quote. The edge still appears in
    # the right-column "Cited by" list; it just won't be a left-column lead.
    return ""


def extract(text: str, self_cite: str = "") -> list[ExtractedCitation]:
    if not text:
        return []
    results: list[ExtractedCitation] = []
    seen: set[str] = set()  # one edge per cited cite per opinion
    for m in NEUTRAL_CITE.finditer(text):
        year = int(m.group("year"))
        if year < 2024:  # neutral citations didn't exist before 2024
            continue
        cite = "%s N.H. %s" % (m.group("year"), m.group("num"))
        if cite == self_cite or cite in seen:
            continue
        seen.add(cite)
        start = m.start()
        context = " ".join(
            text[max(0, start - CONTEXT_PAD):m.end() + CONTEXT_PAD].split()
        )
        results.append(ExtractedCitation(
            reporter_cite=cite,
            text_offset=start,
            context=context,
            quote=_display_quote(text, start, m.end()),
        ))
    results.sort(key=lambda c: c.text_offset)
    return results
