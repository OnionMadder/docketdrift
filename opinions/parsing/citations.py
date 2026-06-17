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
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedCitation:
    """One reference, in an opinion's body, to another case.

    ``reporter_cite`` is normalized to match ``Opinion.reporter_cite`` (e.g.
    ``"2026 N.H. 7"``) so the graph resolves internal edges by exact lookup.
    ``context`` is a text window around the cite, kept for treatment
    classification (Phase 14b) and display.
    """

    reporter_cite: str
    text_offset: int
    context: str


_REGISTRY: dict[str, str] = {
    "NH": "opinions.parsing.citations_nh",
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
