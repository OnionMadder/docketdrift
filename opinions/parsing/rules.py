"""State-keyed dispatcher for court-rule citation extraction.

Mirrors ``opinions/parsing/statutes.py`` deliberately -- same registry
shape, same lazy import, same slug-prefix trick -- so there is one
pattern to learn rather than two.

A court rule is a DIFFERENT KIND OF RECORD from a statute, which is why
this is a separate dispatcher writing to a separate table rather than
another root in the statute slug namespace. Minn. R. Civ. App. P. 109.02
is the in-forma-pauperis rule; Minn. Stat. 109.02 is something else
entirely. Filing rules under a page labeled "statute", or behind an MCP
tool described as returning statutes, would mislabel the source of law
in exactly the way calling extraction "summarizing" mislabels the text.

Currently registered:

  - MN: ``Minn. R. <set> <NN.NN>[, subd. N][(x)]`` + administrative
    ``Minn. R. <NNNN.NNNN>[, subp. N]``

Adding a state: implement ``opinions/parsing/rules_<code>.py`` with an
``extract(text)`` returning RuleRef-shaped records, register it below,
and add its slug prefix. NH, AZ and LA are expected to have the same
blind spot MN did -- nobody has measured their rule vocabulary yet, and
per this repo's own history an unmeasured assumption about what courts
"rarely" write is how a citation layer gets capped for months.
"""
import importlib

_REGISTRY: dict[str, str] = {
    "MN": "opinions.parsing.rules_mn",
}

# Same mechanism as statutes.SLUG_PREFIXES, and for the same measured
# reason: joining a citation table back to the 2.75GB opinions table to
# read court_id took 56.4s on Louisiana and was returning a hard 500 on
# /sitemap-statutes.xml. Filter on the slug prefix instead.
#
# A state absent from this map falls back to the slow-but-correct join
# rather than silently serving an empty result.
SLUG_PREFIXES: dict[str, str] = {
    "MN": "minn.r.",
}

_cache: dict[str, object] = {}


def _load(state_code: str):
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


def extract_rules(state_code: str, text: str):
    """Every rule citation in ``text`` for the state, sorted by offset.

    NOT deduplicated -- one record per occurrence, like the statute
    layer, so a rule page can pull surrounding context per hit.

    Returns ``[]`` for an unregistered state, which callers can read as
    "no rule graph for this state yet" rather than crashing.
    """
    if not text:
        return []
    module = _load(state_code)
    if module is None:
        return []
    return module.extract(text)


def rule_set_label(state_code: str, rule_set: str, long_form: bool = False) -> str:
    """Canonical display label for a rule set, or "" if unknown.

    Returning "" rather than a guess is load-bearing. The statute page
    reconstructed citations with the wrong state's grammar and rendered
    `A.R.S. 13-1103` as "section 13.1103"; no label is honest, an
    invented one is a misstated citation.
    """
    module = _load(state_code)
    hook = getattr(module, "rule_set_label", None)
    if hook is None:
        return ""
    return hook(rule_set, long_form=long_form)


__all__ = ["extract_rules", "rule_set_label", "SLUG_PREFIXES"]
