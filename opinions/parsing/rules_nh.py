# -*- coding: utf-8 -*-
"""New Hampshire court-rule (and administrative-rule) citation extractor.

Vocabulary MEASURED 2026-10-15 on ~1,200 random post-1990 NH opinions
(loose anchor-then-rank capture; tables in docs/TODO.md). What differs
from Minnesota and Arizona, each observed rather than assumed:

  * NO state anchor on the courts' own rules. NH writes ``Sup. Ct. R.
    20(2)`` and ``Super. Ct. R. 59-A`` -- never "N.H. Sup. Ct. R." -- so
    the set name IS the anchor. A United States Supreme Court rule is
    excluded by a lookbehind.
  * LETTERED rule numbers: ``16-A``, ``59-A``, ``1.25-A``. The letter is
    part of the number (Rule 16-A is a different rule from Rule 16), so
    it belongs in the slug.
  * The Superior Court has TWO numberings: the old Superior Court Rules
    and the 2013+ Superior Court Civil Rules (``Super. Ct. Civ. R.``).
    Rule 51 under one is not Rule 51 under the other, so they are
    separate sets.
  * Administrative rules carry an AGENCY PREFIX before the number:
    ``N.H. Admin. R., Env-Ws 386.58``. Kept as their own set, exactly as
    Minnesota's administrative rules are.
  * Trap: ``N.H.H.R. Jour.`` is the House Journal, not a rule. No
    pattern here can match it (none anchors on a bare "R.").

THE NOTICE. Every recent NH opinion opens "NOTICE: This opinion is
subject to motions for rehearing under Rule 22 as well as formal
revision before publication in the New Hampshire Reports." -- 100% in
the first 400 chars, ~15% of opinions. It cites a BARE "Rule 22", which
this full-cite grammar never extracts (bare "Rule N" is a later tier
here as in MN and AZ), so it cannot pollute a count. The guard below
flags it anyway if a set-named form ever appears in that sentence, and a
test pins that the notice yields no row at all.

``Sup. Ct. R. 20(2)`` ("has determined to resolve the case by way of
this order") is COUNTED: it records how the court decided the case, the
same line as a jurisdiction recital. Editorial call, 2026-10-16.
"""
import re
from collections import namedtuple

RuleRef = namedtuple(
    "RuleRef",
    "reference_slug reference_display rule_set rule_number subdivision "
    "subsection is_boilerplate text_offset",
)

SLUG_ROOT = "nh.r."

_R = r"R(?:ules?\b|\.)"          # "R." or "Rule" / "Rules"

# key, display (the NH courts' own house style), long name, prefix regex.
# Order matters only for documentation; longest match per offset wins.
RULE_SETS = [
    ("super.ct.civ", "Super. Ct. Civ. R.", "New Hampshire Superior Court Civil Rules",
     r"\bSuper(?:ior)?\.?\s*C(?:our)?t\.?\s*Civ(?:il)?\.?\s*" + _R),
    ("super.ct", "Super. Ct. R.", "New Hampshire Superior Court Rules",
     r"\bSuper(?:ior)?\.?\s*C(?:our)?t\.?\s*" + _R),
    ("sup.ct", "Sup. Ct. R.", "New Hampshire Supreme Court Rules",
     r"(?<!U\.S\.\s)(?<!U\.S\.)(?<!States\s)(?<!Federal\s)\b(?:N\.\s?H\.\s*)?"
     r"Sup(?:reme)?\.?\s*C(?:our)?t\.?\s*" + _R),
    ("fam.div", "Fam. Div. R.", "New Hampshire Circuit Court Family Division Rules",
     r"\bFam(?:ily)?\.?\s*Div(?:ision)?\.?\s*" + _R),
    ("dist.div", "Dist. Div. R.", "New Hampshire Circuit Court District Division Rules",
     r"\bDist(?:rict)?\.?\s*Div(?:ision)?\.?\s*" + _R),
    ("prob.div", "Prob. Div. R.", "New Hampshire Circuit Court Probate Division Rules",
     r"\bProb(?:ate)?\.?\s*Div(?:ision)?\.?\s*" + _R),
    ("evid", "N.H. R. Ev.", "New Hampshire Rules of Evidence",
     r"\b(?:N\.\s?H\.\s*R\.\s*Ev(?:id)?\.?"
     r"|(?:New\s+Hampshire|N\.\s?H\.)\s+Rules?\s+of\s+Evidence)"),
    ("prof.conduct", "N.H. R. Prof. Conduct", "New Hampshire Rules of Professional Conduct",
     r"\b(?:N\.\s?H\.\s*R\.\s*Prof(?:\.|essional)\s*Conduct"
     r"|(?:New\s+Hampshire|N\.\s?H\.)\s+Rules?\s+of\s+Professional\s+Conduct)"),
    ("crim.p", "N.H. R. Crim. P.", "New Hampshire Rules of Criminal Procedure",
     r"\b(?:N\.\s?H\.\s*R\.\s*Crim\.?\s*P\.?"
     r"|(?:New\s+Hampshire|N\.\s?H\.)\s+Rules?\s+of\s+Criminal\s+Procedure)"),
]

RULE_SET_DISPLAY = {k: d for k, d, _l, _p in RULE_SETS}
RULE_SET_LONG = {k: l for k, _d, l, _p in RULE_SETS}
RULE_SET_DISPLAY["admin"] = "N.H. Admin. R."
RULE_SET_LONG["admin"] = "New Hampshire Code of Administrative Rules"

# 20, 16-A, 1.25-A, 59-A. The letter suffix only ever follows a hyphen
# and is a single capital, so "27-28" (a range) never reads as a rule.
#
# A RANGE ("Rule 27-28", "Rules 7-9") is refused outright: we cannot
# store a range and must never invent a cite to its first member.
_NUM = r"(\d{1,3}(?:\.\d{1,3})?(?:-[A-Z]\b)?)(?!\d|\.\d|\s?[-–]\s?\d)"

# Same subsection grammar as Arizona: only the first level may follow a
# space, deeper levels must be adjacent, and an edition year is refused.
_PAREN = r"\((?!(?:19|20)\d{2}\))[A-Za-z0-9.]{1,5}\)"
_TAIL = r"(?P<subsection>\s?" + _PAREN + r"(?:" + _PAREN + r"){0,3})?"

# A defined-term alias between the set and the number: New Hampshire
# Rule of Evidence (Rule) 702.
_ALIAS = r"(?:\s*\(\s*[“\"”']?\s*[A-Za-z]{2,6}\s*[“\"”']?\s*\))?"

_SET_PATTERNS = [
    (key, re.compile(prefix + _ALIAS + r"\s*" + _NUM + _TAIL, re.I))
    for key, _d, _l, prefix in RULE_SETS
]

# Administrative rules: "N.H. Admin. R., Env-Ws 386.58",
# "New Hampshire Administrative Rules, Ed 204.01(b)(4)",
# "N.H. CODE ADMIN. R., Rab 202.06". NOT case-insensitive as a whole:
# the agency prefix's capitalization (Env-Ws, He-W, Saf-C) is how it is
# told apart from an ordinary word.
_ADMIN_RE = re.compile(
    r"(?:N\.\s?H\.|New\s+Hampshire)\s*(?:C(?:ODE|ode)\s+)?"
    r"(?:A(?:DMIN|dmin)(?:ISTRATIVE|istrative)?\.?)\s*(?:R(?:ULES|ules)\b|R\.)\s*,?\s*"
    r"(?P<agency>[A-Z][a-z]{1,3}(?:-[A-Z][A-Za-z]{0,2})?)\s+"
    r"(?P<num>\d{1,4}(?:\.\d{1,3})?)" + _TAIL
)

# Belt and braces for the rehearing notice (see the module docstring).
_NOTICE_CUE = re.compile(r"motions?\s+for\s+rehearing|formal\s+revision\s+before\s+publication", re.I)


def _is_boilerplate(text, start, rule_set, number):
    if rule_set != "sup.ct" or number != "22" or start >= 600:
        return False
    return bool(_NOTICE_CUE.search(text[max(0, start - 120):start + 160]))


def _build(rule_set, number, m, start, text):
    sec = re.sub(r"\s+", "", m.groupdict().get("subsection") or "")
    number = re.sub(r"\s+", "", number).upper() if "-" in number else number
    slug = (SLUG_ROOT + rule_set + "." + number).lower()
    display = RULE_SET_DISPLAY[rule_set] + " " + number + sec
    return RuleRef(
        reference_slug=slug,
        reference_display=display,
        rule_set=rule_set,
        rule_number=number,
        subdivision="",
        subsection=sec,
        is_boilerplate=_is_boilerplate(text, start, rule_set, number),
        text_offset=start,
    )


def extract(text):
    """Every NH rule citation in ``text``, one RuleRef per occurrence."""
    if not text:
        return []
    found = {}  # offset -> (match length, RuleRef); longest wins per start
    for rule_set, pattern in _SET_PATTERNS:
        for m in pattern.finditer(text):
            prev = found.get(m.start())
            if prev is None or len(m.group(0)) > prev[0]:
                found[m.start()] = (len(m.group(0)),
                                    _build(rule_set, m.group(1), m, m.start(), text))
    for m in _ADMIN_RE.finditer(text):
        if m.start() in found:
            continue
        agency = m.group("agency")
        number = agency + " " + m.group("num")
        sec = re.sub(r"\s+", "", m.group("subsection") or "")
        found[m.start()] = (len(m.group(0)), RuleRef(
            reference_slug=(SLUG_ROOT + "admin." + agency + "." + m.group("num")).lower(),
            reference_display="N.H. Admin. R., " + number + sec,
            rule_set="admin",
            rule_number=number[:16],
            subdivision="",
            subsection=sec,
            is_boilerplate=False,
            text_offset=m.start(),
        ))
    # "Super. Ct. Civ. R. 51" also matches the shorter "Super. Ct. R."
    # prefix? No -- "Civ." sits between, so the shorter pattern fails.
    # Overlapping offsets are resolved above by length.
    return [ref for _n, ref in (found[k] for k in sorted(found))]


# Bare search query: "Rule 16-A", "Rule 20(2)", "1.25". Dot OR an
# explicit "Rule" marker required, as in Arizona.
BARE_RULE_RE = re.compile(
    r"^\s*(?:(?P<marker>rule)\s*)?(?P<number>\d{1,3}(?:\.\d{1,3})?(?:-[A-Za-z])?)"
    r"(?P<tail>(?:\s?\([^)\s]{1,6}\)){0,4})\s*$",
    re.IGNORECASE,
)


def bare_rule_number(query):
    if not query:
        return None
    m = BARE_RULE_RE.match(query)
    if m is None:
        return None
    number = m.group("number").upper()
    if "." not in number and not m.group("marker"):
        return None
    return number, number + (m.group("tail") or "").replace(" ", "")


def bare_slug_candidates(rule_set, body):
    label = RULE_SET_DISPLAY.get(rule_set)
    parsed = bare_rule_number(body if "." in body else "rule " + body)
    if not label or rule_set == "admin" or parsed is None:
        return []
    found = [r for r in extract(label + " " + body) if r.rule_set == rule_set]
    if not found or found[0].rule_number != parsed[0]:
        return []
    return [found[0].reference_slug]


def rule_set_label(rule_set, long_form=False):
    table = RULE_SET_LONG if long_form else RULE_SET_DISPLAY
    return table.get(rule_set, "")
