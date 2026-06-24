"""Citation-treatment classifier -- how a citing opinion treats a cited case.

Sentence-level cue regexes over the citation's stored context window. This is
the "Shepard's signal" layer: did the court FOLLOW the cited case, DISTINGUISH
it, OVERRULE it, or just cite it?

Deliberately CONSERVATIVE -- legal prose is full of words like "following" and
"explained" used in non-treatment senses, so the cues require treatment-
specific phrasing (usually a first-person-plural court voice: "we overrule",
"we decline to follow"). Anything without a clear cue stays CITED. Priority:
the strongest signal wins (negative treatments before positive).

State-agnostic: the cue phrases are the same across jurisdictions, so there's
one classifier rather than per-state modules.
"""
import re

# (treatment, compiled pattern). Checked in order; first match wins.
_CUES = [
    # Negative treatments first; strongest signal wins. Each cue requires the
    # court's OWN voice acting on the cited case. Bare word-stems ("overrule",
    # "distinguish") appear constantly in ordinary legal prose -- "distinguish
    # between", "decline to overrule", "not persuaded that" -- so matching them
    # raw fabricates Shepard's signals. (NH proving ground: a "we have long
    # distinguished between..." passage mislabeled an approving cite as
    # DISTINGUISHED; that false positive is what tightened these.)
    ("OVERRULED", re.compile(
        r"\bwe\s+(?:hereby\s+|now\s+|today\s+|therefore\s+)?overrule(?:s|d)?\b"
        r"|\b(?:is|are|was|were)\s+(?:hereby\s+)?overruled\b"
        r"|\bhereby\s+overrul(?:e|ed)\b"
        r"|\bno\s+longer\s+good\s+law\b", re.I)),
    ("DISTINGUISHED", re.compile(
        # adjective ("X is distinguishable [from]") or court-voice "we
        # distinguish" -- but never "...between" (differentiating concepts,
        # not treating a case).
        r"\bdistinguishable\b(?!\s+between\b)"
        r"|\bwe\s+(?:must\s+|now\s+|therefore\s+|thus\s+|further\s+|again\s+|also\s+)?distinguish(?:es|ed)?\b(?!\s+between\b)", re.I)),
    ("CRITICIZED", re.compile(
        r"\bdeclin(?:e|ed|es|ing)\s+to\s+follow\b"
        r"|\bcriticiz(?:e|ed|es|ing)\b"
        # "not persuaded BY [authority]" is criticism; "not persuaded THAT
        # [proposition]" is not -- require the directed "by".
        r"|\bwe\s+(?:are\s+)?not\s+persuaded\s+by\b", re.I)),
    ("FOLLOWED", re.compile(
        r"\bwe\s+(?:follow|adhere\s+to|reaffirm|again\s+hold|are\s+bound\s+by)\b"
        r"|\bin\s+accordance\s+with\s+our\s+(?:holding|decision)\b", re.I)),
    ("EXPLAINED", re.compile(
        r"\bas\s+(?:we\s+)?explained\s+in\b"
        r"|\bwe\s+(?:explained|clarified)\b", re.I)),
]


def classify_treatment(context: str) -> str:
    """Return a Treatment value ('OVERRULED' / ... / 'CITED') for a context."""
    if context:
        for treatment, pattern in _CUES:
            if pattern.search(context):
                return treatment
    return "CITED"


__all__ = ["classify_treatment"]
