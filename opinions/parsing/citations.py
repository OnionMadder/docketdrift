"""State-keyed dispatcher for CASE-citation extraction -- the citation graph.

Where ``statutes.py`` pulls statute references, this pulls references to
OTHER opinions out of an opinion's body. Each extractor returns
``ExtractedCitation`` rows whose ``reporter_cite`` is normalized to the exact
form stored in ``Opinion.reporter_cite``, so ``extract_citations`` can resolve
internal edges (cite -> opinion in our corpus) by exact match.

Currently registered:
  - NH: neutral cites ``<year> N.H. <n>`` (the resolvable 2024+ era)

Adding a state: implement ``opinions/parsing/citations_<code>.py`` exposing
``extract(text, self_cite="") -> list[ExtractedCitation]`` and add a row to
``_REGISTRY``. MN/AZ wait on a reporter-cite backfill (their cites aren't in
our opinion text), so they have no extractor yet.
"""
import importlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedCitation:
    """One reference, in an opinion's body, to another case.

    ``reporter_cite`` is normalized to match ``Opinion.reporter_cite`` (e.g.
    ``"2026 N.H. 7"``) so the graph resolves internal edges by exact lookup.
    ``context`` is a raw text window around the cite, kept for treatment
    classification (Phase 14b). ``quote`` is a cleaner, sentence-trimmed
    passage for the public "How this document has been cited" display --
    empty when no usable sentence was found.
    """

    reporter_cite: str
    text_offset: int
    context: str
    quote: str = ""


# Candidate sentence terminator: ". ", "? ", "! ", with an optional closing
# quote/paren. Many of these are FALSE boundaries in legal prose because of
# abbreviations that end in a period -- "v.", "N.H.", "Inc.", single-letter
# initials -- so _real_boundaries() filters them out (see below).
_BOUNDARY = re.compile(r'[.!?]["\')\]]?\s')

# Abbreviations (dots stripped, lowercased) whose trailing period does NOT end
# a sentence. Heavy on the ones that appear inside case names + reporter cites,
# since those are the costly false splits for quote extraction.
_ABBREVS = frozenset({
    "v", "vs", "nh", "us", "inc", "co", "corp", "cos", "ltd", "llc", "no",
    "nos", "stat", "const", "art", "jr", "sr", "dr", "mr", "mrs", "ms", "cf",
    "ch", "para", "paras", "pp", "ann", "rev", "supp", "ed", "al", "etc",
    "cir", "ct", "app", "div", "cj", "eg", "ie", "vt", "me", "mass", "conn",
    "ri", "ariz", "minn", "dept", "assn", "bros", "est",
})


def _token_before(text: str, dot_pos: int) -> str:
    """The alnum/dot run immediately before the period at ``dot_pos``."""
    k = dot_pos
    while k > 0 and (text[k - 1].isalnum() or text[k - 1] in ".'-"):
        k -= 1
    return text[k:dot_pos]


def _real_boundaries(text: str) -> list[int]:
    """End offsets of true sentence boundaries in ``text``.

    Filters out periods that belong to a known abbreviation or a single-letter
    initial ("A.", "N.H.", "v."), which would otherwise split mid-case-name.
    ``!``/``?`` are always boundaries.
    """
    out = []
    for m in _BOUNDARY.finditer(text):
        if text[m.start()] == ".":
            tok = _token_before(text, m.start()).replace(".", "").lower()
            if tok in _ABBREVS or (len(tok) == 1 and tok.isalpha()):
                continue
        out.append(m.end())
    return out


def sentence_window(text: str, start: int, end: int,
                    max_back: int = 320, max_fwd: int = 320) -> str:
    """Return the sentence(s) of ``text`` spanning ``[start, end)``, bounded.

    Expands left to the previous (real) sentence boundary within ``max_back``
    chars and right to the next within ``max_fwd``, then normalizes whitespace.
    Used to lift a clean display quote around a citation whose offset we know.
    """
    if not text:
        return ""
    lo = max(0, start - max_back)
    left = _real_boundaries(text[lo:start])
    s = lo + left[-1] if left else lo
    hi = min(len(text), end + max_fwd)
    right = _real_boundaries(text[end:hi])
    e = end + right[0] if right else hi
    return " ".join(text[s:e].split())


_REGISTRY: dict[str, str] = {
    "NH": "opinions.parsing.citations_nh",
    "MN": "opinions.parsing.citations_mn",
    "AZ": "opinions.parsing.citations_az",
}

_cache: dict[str, object] = {}


def _load(state_code: str):
    state_code = (state_code or "").upper()
    if state_code in _cache:
        return _cache[state_code]
    module_path = _REGISTRY.get(state_code)
    module = importlib.import_module(module_path) if module_path else None
    _cache[state_code] = module
    return module


def extract_citations(state_code: str, text: str, self_cite: str = "") -> list[ExtractedCitation]:
    """Find every case citation in ``text`` for the given state.

    ``self_cite`` is the citing opinion's OWN ``reporter_cite`` -- excluded so
    an opinion's caption/header doesn't make it "cite" itself. Returns ``[]``
    when no extractor is registered for the state.
    """
    if not text:
        return []
    module = _load(state_code)
    if module is None:
        return []
    return module.extract(text, self_cite=self_cite)


__all__ = ["ExtractedCitation", "extract_citations"]
