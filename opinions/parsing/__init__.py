"""State-keyed registry of opinion parsers.

A `StateParser` knows the structural conventions of one state's appellate
opinions and turns plain text (from PDF extraction or CL bulk data) into a
structured ``ParsedOpinion``. Parsers are intentionally regex+heuristics
for v1: deterministic, debuggable, no API budget, and we can audit every
extraction rule by reading the source. We may layer LLM extraction on top
later for fields the regex pass can't cover.

Usage:

    from opinions.parsing import parse
    result = parse("MN", raw_text)   # -> ParsedOpinion or None

Adding a new state: implement ``StateParser`` in a new module (e.g.
``wi.py``), add it to ``REGISTRY`` below, and write fixture-based tests
that pin the expected ParsedOpinion against a handful of real opinions.
"""
from __future__ import annotations

from .az import ArizonaParser
from .base import ParsedOpinion, StateParser
from .la import LouisianaParser
from .mn import MinnesotaParser
from .nh import NewHampshireParser

REGISTRY: dict[str, StateParser] = {
    "MN": MinnesotaParser(),
    "NH": NewHampshireParser(),
    "AZ": ArizonaParser(),
    "LA": LouisianaParser(),
}


def parse(state_code: str | None, raw_text: str) -> ParsedOpinion | None:
    """Run the registered parser for ``state_code`` over ``raw_text``.

    Returns ``None`` (not an empty ``ParsedOpinion``) when no parser is
    registered for the state -- this lets callers distinguish "parser ran
    and found nothing" from "no parser available". Returns ``None`` too
    when ``raw_text`` is empty.
    """
    if not state_code or not raw_text:
        return None
    parser = REGISTRY.get(state_code.upper())
    if parser is None:
        return None
    return parser.parse(raw_text)


# The court prints its publication notice at the top of the document.
NONPRECEDENTIAL_HEAD_CHARS = 3000


def text_says_nonprecedential(state_code: str | None, raw_text: str) -> bool:
    """True when the opinion's OWN header marks it nonprecedential.

    Loaders must let this override CourtListener's ``precedential_status``:
    CL reports MN unpublished opinions as "Published" (verified on the
    live API 2026-10-24 for A25-0112, A24-1781, A24-1325), which left
    ~190 of them flagged precedential. Only the HEAD is parsed, so a body
    quoting the notice can't flip a published opinion, and each state
    parser's own guards apply (AZ: COA only). Never returns True from
    silence; absence of the notice is not evidence either way.
    """
    if not raw_text:
        return False
    result = parse(state_code, raw_text[:NONPRECEDENTIAL_HEAD_CHARS])
    return bool(result is not None and result.is_precedential is False)


__all__ = ["ParsedOpinion", "StateParser", "REGISTRY", "parse",
           "text_says_nonprecedential"]
