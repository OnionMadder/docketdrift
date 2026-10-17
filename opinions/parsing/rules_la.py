# -*- coding: utf-8 -*-
"""Louisiana court-rule citation extractor.

Vocabulary MEASURED 2026-10-15 (~1,200 random post-1990 LA opinions; the
tables are in docs/TODO.md). Louisiana cites court rules far less than
the other states (~7% of opinions vs 56-75%), and the shapes are its own:

  * Uniform Rules -- Courts of Appeal: ``Rule 2-16.3``, ``Rule 2-12.4``,
    ``Rule 1-3`` -- a hyphenated chapter-rule number, written after the
    set name with FIVE separator characters seen in real text (``—``,
    ``-``, ``–``, cp1252 ``\\x97`` and the private-use ``\\uf0be``) and
    sometimes as the acronym ``URCA``.
  * Supreme Court Rules in ROMAN NUMERALS with sections: ``Rule XIX,
    § 19``, ``Rule X § 5``, ``Rule IV, Part 2, § 3``, ``La.S.Ct.R. X § 5``.
    The section belongs in the slug -- Rule XIX § 19 (interim suspension)
    and Rule XIX § 20.1 (resignation) are different provisions.
  * District Court Rules: ``La. Dist. Ct. R. 9.14``.
  * Rules of Professional Conduct, cited in the Supreme Court's own
    discipline opinions: ``Rule 8.4(a) of the Rules of Professional
    Conduct``, ``Rules of Professional Conduct: Rules 1.3``.

``La. R.S.`` is the STATUTE layer and is never matched here.

TWO NOTICES, flagged per occurrence (editorial calls, 2026-10-16):
  * "This opinion is NOT DESIGNATED FOR PUBLICATION. Uniform Rules--Courts
    of Appeal, Rule 2-16.3." -- 100% in the last 800 characters.
  * "Pursuant to Rule IV, Part 2, § 3, Watson, J. was not on panel" -- a
    note about who sat, not the court relying on a rule.
Rows are KEPT, excluded from counts, and the total is disclosed.
"""
import re
from collections import namedtuple

RuleRef = namedtuple(
    "RuleRef",
    "reference_slug reference_display rule_set rule_number subdivision "
    "subsection is_boilerplate text_offset",
)

SLUG_ROOT = "la.r."

# Displays use the forms Louisiana courts themselves write, never an
# abbreviation invented here.
RULE_SET_DISPLAY = {
    "urca": "Uniform Rules—Courts of Appeal, Rule",
    "sup.ct": "La. S. Ct. Rule",
    "dist.ct": "La. Dist. Ct. R.",
    "prof.conduct": "Rules of Professional Conduct, Rule",
}
RULE_SET_LONG = {
    "urca": "Uniform Rules of the Louisiana Courts of Appeal",
    "sup.ct": "Rules of the Supreme Court of Louisiana",
    "dist.ct": "Louisiana District Court Rules",
    "prof.conduct": "Louisiana Rules of Professional Conduct",
}

_DASH = "[-–—―\x97]"
_PAREN = r"\((?!(?:19|20)\d{2}\))[A-Za-z0-9.]{1,5}\)"
_TAIL = r"(?P<subsection>\s?" + _PAREN + r"(?:" + _PAREN + r"){0,3})?"

# Uniform Rules -- Courts of Appeal, Rule 2-16.3 / URCA, Rule 2-12.4(B)(1)
# / Uniform Rules of (the) (Louisiana) Courts of Appeal, Rule 1-3 /
# Uniform Rules--Courts of Appeal 2-12.4 (no "Rule"). Also number-first:
# "Rule 2-16.3, Uniform Rules, Courts of Appeal." All from real text.
_URCA_SET = (r"(?:\bUniform\s+Rules?\s*,?\s*(?:of\s+(?:the\s+)?(?:Louisiana\s+)?)?"
             r"(?:" + _DASH + r"|\s)*\s*Courts?\s+of\s+Appeals?|\bURCA)")
_URCA_NUM = (r"(?P<num>\d{1,2}\s?" + _DASH + r"\s?\d{1,2}(?:\.\d{1,2})?)"
             r"(?![\d.]\d|\s?" + _DASH + r"\s?\d)")
_URCA_RE = re.compile(
    _URCA_SET + r"\s*[,.]?\s*(?:Rules?\s+)?" + _URCA_NUM + _TAIL, re.I)
_URCA_FIRST_RE = re.compile(
    r"\bRule\s+" + _URCA_NUM + _TAIL + r"\s*,?\s*(?:of\s+the\s+)?" + _URCA_SET, re.I)

# Supreme Court Rule XIX, § 19 / Rule X § 5 / La.S.Ct.R. X § 5 /
# Rule IV, Part 2, § 3. Requires a Supreme-Court anchor OR the "Part N,
# § N" shape that only Rule IV uses, so a bare Roman "Rule X" in prose
# never matches.
_ROMAN = r"(?P<roman>[IVXL]{1,6})"
_SECTION = (r"(?:\s*,?\s*Part\s+(?P<part>\d|I{1,3}|IV)\s*,?)?"
            r"\s*,?\s*(?:§|ß|[Ss]ec(?:tion|\.)?)\s*(?P<sec>\d{1,2}(?:\.\d{1,2})?)")
_SUPCT_RE = re.compile(
    r"(?:(?:\bLa\.?\s*)?S(?:up(?:reme)?)?\.?\s*C(?:our)?t\.?\s*(?:Rules?|R\.)"
    r"|\bLouisiana\s+Supreme\s+Court\s+Rules?)\s*" + _ROMAN + r"\b" + _SECTION + _TAIL)
_RULE_IV_RE = re.compile(
    r"\bRule\s+(?P<roman>IV)\s*,?\s*Part\s+(?P<part>\d|I{1,3})\s*,?\s*"
    r"(?:§|ß|[Ss]ec(?:tion|\.)?)\s*(?P<sec>\d{1,2})" + _TAIL)

# La. Dist. Ct. R. 9.14
_DISTCT_RE = re.compile(
    r"\bLa\.?\s*Dist(?:rict)?\.?\s*C(?:our)?t\.?\s*(?:Rules?|R\.)\s*"
    r"(?P<num>\d{1,2}\.\d{1,2})(?![\d.]\d)" + _TAIL, re.I)

# Rules of Professional Conduct. Two orders: "Rule 8.4(a) of the Rules
# of Professional Conduct", and "Rules of Professional Conduct: Rules
# 1.3, ..." (only the FIRST number of a list is a bound cite here).
_PC_NUM = r"(?P<num>\d\.\d{1,2})(?![\d.]\d)"
_PC_AFTER = re.compile(
    r"\bRules?\s+of\s+Professional\s+Conduct\s*[:,]?\s*(?:Rules?\s+)?" + _PC_NUM + _TAIL, re.I)
_PC_BEFORE = re.compile(
    r"\bRule\s+" + _PC_NUM + _TAIL
    + r"\s+of\s+the\s+(?:Louisiana\s+)?Rules\s+of\s+Professional\s+Conduct", re.I)

_NOTICE_2163 = re.compile(r"not\s+designated\s+for\s+publication", re.I)
# The clerk's certificate: "NOTICE OF JUDGMENT AND CERTIFICATE OF
# DELIVERY ... HAS BEEN DELIVERED IN ACCORDANCE WITH UNIFORM RULES -
# COURT OF APPEAL, RULE 2-16.4 AND 2-16.5 THIS DAY ..." / "... HAS BEEN
# TRANSMITTED IN ACCORDANCE WITH ... RULE 4-6 THIS DAY". A statement
# about delivering the document, not the court relying on a rule -- the
# same class as the 2-16.3 notice. Found in real output 2026-10-16.
_DELIVERY_RULES = {"2-16.4", "2-16.5", "4-6"}
_NOTICE_DELIVERY = re.compile(
    r"(?:delivered|transmitted|mailed)\s+in\s+accordance\s+with|this\s+day\s+\d|"
    r"certificate\s+of\s+(?:delivery|mailing)|notice\s+of\s+judgment", re.I)
_NOTICE_IV = re.compile(
    r"not\s+on\s+(?:the\s+)?panel|did\s+not\s+(?:participate|sit)|recus|"
    r"(?:pro\s+tempore|ad\s+hoc)|sitting\s+(?:for|in\s+place)|assigned", re.I)


def _norm_dash(s):
    return re.sub(_DASH, "-", re.sub(r"\s+", "", s))


def _ref(rule_set, number, display_num, sec, start, boiler):
    return RuleRef(
        reference_slug=(SLUG_ROOT + rule_set + "." + number).lower(),
        reference_display=RULE_SET_DISPLAY[rule_set] + " " + display_num + sec,
        rule_set=rule_set,
        rule_number=number[:16],
        subdivision="",
        subsection=sec,
        is_boilerplate=boiler,
        text_offset=start,
    )


def extract(text):
    """Every Louisiana rule citation in ``text``, one per occurrence."""
    if not text:
        return []
    found = {}

    def put(start, length, ref):
        prev = found.get(start)
        if prev is None or length > prev[0]:
            found[start] = (length, ref)

    for pattern in (_URCA_RE, _URCA_FIRST_RE):
        for m in pattern.finditer(text):
            num = _norm_dash(m.group("num"))
            sec = re.sub(r"\s+", "", m.group("subsection") or "")
            win = text[max(0, m.start() - 200):m.end() + 60]
            boiler = ((num == "2-16.3" and bool(_NOTICE_2163.search(win)))
                      or (num in _DELIVERY_RULES and bool(_NOTICE_DELIVERY.search(win))))
            put(m.start(), len(m.group(0)), _ref("urca", num, num, sec, m.start(), boiler))

    for pattern in (_SUPCT_RE, _RULE_IV_RE):
        for m in pattern.finditer(text):
            roman = m.group("roman").upper()
            part = (m.group("part") or "").upper()
            part = {"I": "1", "II": "2", "III": "3", "IV": "4"}.get(part, part)
            number = roman + (".part" + part if part else "") + "." + m.group("sec")
            shown = roman + (", Part " + part if part else "") + ", § " + m.group("sec")
            sec = re.sub(r"\s+", "", m.group("subsection") or "")
            boiler = (roman == "IV" and part == "2"
                      and bool(_NOTICE_IV.search(text[max(0, m.start() - 200):m.end() + 160])))
            put(m.start(), len(m.group(0)),
                _ref("sup.ct", number.lower(), shown, sec, m.start(), boiler))

    for m in _DISTCT_RE.finditer(text):
        sec = re.sub(r"\s+", "", m.group("subsection") or "")
        put(m.start(), len(m.group(0)),
            _ref("dist.ct", m.group("num"), m.group("num"), sec, m.start(), False))

    for pattern in (_PC_AFTER, _PC_BEFORE):
        for m in pattern.finditer(text):
            sec = re.sub(r"\s+", "", m.group("subsection") or "")
            put(m.start(), len(m.group(0)),
                _ref("prof.conduct", m.group("num"), m.group("num"), sec, m.start(), False))

    return [ref for _n, ref in (found[k] for k in sorted(found))]


def rule_set_label(rule_set, long_form=False):
    table = RULE_SET_LONG if long_form else RULE_SET_DISPLAY
    return table.get(rule_set, "")
