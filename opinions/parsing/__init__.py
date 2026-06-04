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

from .base import ParsedOpinion, StateParser
from .mn import MinnesotaParser

REGISTRY: dict[str, StateParser] = {
    "MN": MinnesotaParser(),
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


__all__ = ["ParsedOpinion", "StateParser", "REGISTRY", "parse"]
