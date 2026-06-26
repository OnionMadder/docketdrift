"""Reporter-citation extractor for NON-NH states (MN / AZ / future), built on
``eyecite`` from the Free Law Project (https://github.com/freelawproject/eyecite).

Why a separate extractor from ``citations_nh.py``:

  - NH stays on the hand-tuned homebrew extractor (``citations_nh.py``)
    permanently. NH's 2024+ neutral cites (``<year> N.H. <n>``) are simple,
    self-referential, and resolvable inside our own corpus by exact match, so a
    bespoke regex is both sufficient and a clean reference implementation.
  - Every other state's case law cites by reporter (``123 Minn. 456``,
    ``240 Ariz. 1``, regional reporters, federal reporters). Writing a correct
    per-state reporter tokenizer by hand is a large, error-prone surface that
    eyecite already solves -- it carries the Free Law Project's reporters
    database and the same tokenizer that powers CourtListener's citation graph.

This module is the eyecite WRAPPER ONLY. It does exactly one job: turn opinion
text into ``ExtractedCitation`` rows (the same shape ``citations_nh.py``
returns), so downstream code consumes either extractor interchangeably.
Everything that runs *after* tokenization stays our own custom code:

  - treatment classification -> ``opinions/parsing/treatment.py``
  - neutral/reporter-cite canonicalization + graph resolution + storage
    -> ``extract_citations`` management command + the ``OpinionCitation`` model

PREPARATION INFRASTRUCTURE -- NOT YET WIRED IN. The ``extract_citations``
dispatcher (``citations.py:_REGISTRY``) does not route any state here yet.
Migrating MN/AZ onto this is a separate future task gated on the CourtListener
reporter-cite backfill (MN/AZ reporter cites are assigned post-publication and
aren't in our opinion text, so there's nothing to resolve against until that
lands). This module just needs to exist and be importable so that migration is
a wiring change, not a build.
"""
from __future__ import annotations

from .citations import ExtractedCitation

# Context window (chars each side of the cite), matched to citations_nh.py so
# the treatment classifier sees an equivalent window from either extractor.
CONTEXT_PAD = 180


def _normalize(citation) -> str:
    """Best-effort canonical reporter cite for an eyecite citation.

    eyecite's ``corrected_citation()`` normalizes the reporter abbreviation and
    spacing (e.g. ``123 minn. 456`` -> ``123 Minn. 456``). Fall back to the raw
    matched text if a citation subtype doesn't implement it.
    """
    corrected = getattr(citation, "corrected_citation", None)
    if callable(corrected):
        try:
            value = corrected()
            if value:
                return value.strip()
        except Exception:
            pass
    return citation.matched_text().strip()


def extract(text: str, self_cite: str = "") -> list[ExtractedCitation]:
    """Pull reporter case-citations from ``text`` via eyecite.

    Mirrors ``citations_nh.extract``: returns ``ExtractedCitation`` rows sorted
    by position, one edge per distinct cite, excluding the opinion's own cite.
    Only FULL case citations are returned -- short forms (``id.``, ``supra``,
    ``123 Minn. at 460``) point back to an antecedent in the same opinion and
    don't independently resolve to a target in our corpus, so they'd add
    false-positive noise to the graph the same way pre-neutral cites do for NH.
    eyecite is imported lazily so importing this module never pulls eyecite's
    native deps into a process that won't use it.
    """
    if not text:
        return []

    # Lazy import: keeps eyecite (and its C-extension deps) out of any process
    # that imports this module without calling extract().
    from eyecite import get_citations
    from eyecite.models import FullCaseCitation

    self_cite = (self_cite or "").strip()
    results: list[ExtractedCitation] = []
    seen: set[str] = set()  # one edge per cited cite per opinion

    for citation in get_citations(text):
        if not isinstance(citation, FullCaseCitation):
            continue
        cite = _normalize(citation)
        if not cite or cite == self_cite or cite in seen:
            continue
        seen.add(cite)
        start, end = citation.span()
        context = " ".join(
            text[max(0, start - CONTEXT_PAD):end + CONTEXT_PAD].split()
        )
        results.append(ExtractedCitation(
            reporter_cite=cite,
            text_offset=start,
            context=context,
        ))

    results.sort(key=lambda c: c.text_offset)
    return results


__all__ = ["extract"]
