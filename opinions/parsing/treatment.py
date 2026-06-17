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
    ("OVERRULED", re.compile(
        r"\boverrul(?:e|ed|es|ing)\b"
        r"|\bis\s+(?:hereby\s+)?overruled\b"
        r"|\bno\s+longer\s+good\s+law\b", re.I)),
    ("DISTINGUISHED", re.compile(
        r"\bdistinguish(?:able|ed|es|ing)?\b", re.I)),
    ("CRITICIZED", re.compile(
        r"\bdeclin(?:e|ed|es|ing)\s+to\s+follow\b"
        r"|\bcriticiz(?:e|ed|es|ing)\b"
        r"|\bwe\s+(?:are\s+)?not\s+persuaded\b", re.I)),
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
