"""State-keyed dispatcher for statute citation extraction.

Each state's statute syntax is different enough that a single regex
doesn't cover them all without false positives. We keep one per-state
extractor module per file, exposing a uniform ``extract(text) ->
list[ExtractedStatute]`` interface, and dispatch by state code here.

Currently registered:

  - MN: ``Minn. Stat. § <chapter>.<section>[, subd. <n>]`` + chapter-only
  - NH: ``RSA <chapter>:<section>[, <Roman-subdivision>]``
  - AZ: ``A.R.S. § <title>-<section>[(<subsection>)]``

Adding a new state: implement ``opinions/parsing/statutes_<code>.py``
with an ``extract(text)`` function, then add a row to ``_REGISTRY``
below.

The ``ExtractedStatute`` dataclass is the cross-state storage shape --
each extractor maps its state's local terminology (chapter, title,
subsection, subdivision) onto a common (chapter, section, subdivision)
triple so the StatuteCitation table can index every state's citations
the same way.
"""
import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedStatute:
    """One occurrence of a statute citation in an opinion.

    Cross-state shape: every extractor maps its local grammar onto
    these three slots (chapter + section + subdivision). For AZ, the
    A.R.S. "title" maps onto ``chapter``. For NH, the RSA "chapter"
    (which can have an ``-X`` suffix like 159-B) maps onto ``chapter``
    too. Storage stays uniform; the per-state ``reference_display``
    string carries the canonical Bluebook form for the public page.
    """

    chapter: str
    section: str
    subdivision: str
    reference_slug: str
    reference_display: str
    text_offset: int


# Map state code -> module path. Modules are imported lazily on first
# call so the parsing package's import doesn't drag every state's
# regex compilation into every Django process boot.
_REGISTRY: dict[str, str] = {
    "MN": "opinions.parsing.statutes_mn",
    "NH": "opinions.parsing.statutes_nh",
    "AZ": "opinions.parsing.statutes_az",
    "LA": "opinions.parsing.statutes_la",
}

# Every slug a state's extractor emits begins with that state's own
# marker, so a slug prefix identifies the state WITHOUT joining
# StatuteCitation back to Opinion to read court_id. That join is against
# the 2.75GB opinions table and measured 56.4s for Louisiana -- past the
# 25s cap, which is why /sitemap-statutes.xml was returning a hard 500 on
# LA and MN while AZ and NH happened to squeak under.
#
# VERIFIED EQUIVALENT on prod 2026-09-19, per state, both directions:
# the prefix loses nothing the join finds and leaks nothing from another
# state (MN 10,442 / AZ 15,556 / NH 8,674 / LA 18,132 slugs; 0 lost,
# 0 leaked in every case). LA is "la." rather than "la.rs." because that
# state emits several roots -- la.rs, la.civ, la.ccp, la.crimproc,
# la.const, la.chc, la.evid.
#
# A state missing from this map is NOT a silent empty sitemap: callers
# fall back to the (correct, slow) join. Add a row when adding a state.
SLUG_PREFIXES: dict[str, str] = {
    "MN": "minn.stat.",
    "AZ": "ars.",
    "NH": "rsa.",
    "LA": "la.",
}

_cache: dict[str, object] = {}


def _load(state_code: str):
    """Return the cached extractor module for ``state_code`` or None."""
    state_code = (state_code or "").upper()
    if state_code in _cache:
        return _cache[state_code]
    module_path = _REGISTRY.get(state_code)
    if module_path is None:
        _cache[state_code] = None
        return None
    module = importlib.import_module(module_path)
    _cache[state_code] = module
    return module


def extract_statutes(state_code: str, text: str) -> list[ExtractedStatute]:
    """Find every statute citation in ``text`` for the given state.

    Returns a list (NOT deduplicated) sorted by text_offset. Multiple
    citations of the same statute in the same opinion are preserved
    so the statute page can pull surrounding context for each hit.

    Returns ``[]`` when no extractor is registered for the state --
    callers can use that as a "no statute graph for this state yet"
    signal instead of crashing.
    """
    if not text:
        return []
    module = _load(state_code)
    if module is None:
        return []
    return module.extract(text)


def bare_cite_slugs(state_code: str, query: str) -> list[str]:
    """Slugs a bare section-number search query could mean, best first.

    Optional per-state hook: a state module may define
    ``bare_slug_candidates(query) -> list[str]``. States that haven't
    implemented it return [] and simply keep the old search behavior, so
    this is additive and cannot regress a state it hasn't been written
    for.

    Callers MUST verify each candidate against the corpus before
    redirecting. This function only answers "what could this notation
    mean", never "this statute exists".
    """
    if not query:
        return []
    module = _load(state_code)
    hook = getattr(module, "bare_slug_candidates", None)
    if hook is None:
        return []
    return hook(query)


__all__ = ["ExtractedStatute", "extract_statutes", "bare_cite_slugs"]
