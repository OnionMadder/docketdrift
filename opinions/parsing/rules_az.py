# -*- coding: utf-8 -*-
"""Arizona court-rule (and administrative-code) citation extractor.

Vocabulary MEASURED before this was written (2026-10-15, ~1,200 random
post-1990 AZ opinions, loose anchor-then-rank capture; see the TODO
block). 75% of AZ opinions cite a court rule. The shapes that a regex
written from memory would have missed:

  * ``ARCAP 21`` -- the Arizona Rules of Civil Appellate Procedure are
    usually cited by ACRONYM, with no "Ariz. R." anchor at all (147 of
    1,214 opinions). The same body is also written ``Ariz. R. Civ. App.
    P.`` and spelled out; all three land on ONE slug, because they are
    one body of law under two names.
  * Spacing drifts inside the abbreviation: ``Ariz. R. Crim. P.``,
    ``Ariz. R.Crim. P.``, ``Ariz.R.Crim.P.`` are all common.
  * ``Ariz. R.P. Juv. Ct.`` / ``Ariz. R.P. Spec. Act.`` carry an extra
    "P." (Rules of Procedure for ...).
  * Older opinions cite ``Rule 50, Ariz. R. Civ. P., 16 A.R.S.`` -- the
    number comes BEFORE the set and ``16 A.R.S.`` is the VOLUME of the
    statutes the rules are printed in. The grammar refuses a comma
    between the set and the number, so that 16 can never be stored as
    "Rule 16". Bare ``Rule 32`` (462 opinions) is a later, separate tier,
    as in Minnesota: which body it means comes from context.

Arizona subdivides with parentheses -- ``31.10(a)(7)(A)`` -- not
``subd.``, so the slug stops at the rule number and the whole
parenthetical chain is kept for display. Truncating ``(a)(7)(A)`` to
``(a)(7)`` would point at a broader provision than the court cited.

ONE notice is flagged -- the memorandum-decision header citing Sup. Ct.
R. 111(c); see _is_boilerplate. Measured, not assumed, and it was
MISSED by the first shape test (case-sensitive; the notice is in
capitals) and caught only because Sup. Ct. cites came out 10x too big.
Nothing else is a notice: ``ARCAP 21`` (82% of its
occurrences in the last 800 characters) is the court awarding costs
conditional on compliance -- the court applying the rule, a recurrence
like ``A.R.S. 12-2101(A)``, which stays counted.
"""
import re
from collections import namedtuple

RuleRef = namedtuple(
    "RuleRef",
    "reference_slug reference_display rule_set rule_number subdivision "
    "subsection is_boilerplate text_offset",
)

SLUG_ROOT = "ariz.r."

# key, short display, long display, spelling variants AFTER the anchor.
# Matched longest-first per start offset, so "Civ. App. P." beats
# "Civ. P." on the same text.
RULE_SETS = [
    ("civ.app.p", "Ariz. R. Civ. App. P.", "Arizona Rules of Civil Appellate Procedure", [
        r"Civ\.?\s*App\.?\s*P(?:roc)?\.?", r"of\s+Civil\s+Appellate\s+Procedure",
    ]),
    ("fam.law.p", "Ariz. R. Fam. Law P.", "Arizona Rules of Family Law Procedure", [
        r"Fam\.?\s*Law\s*P(?:roc)?\.?", r"of\s+Family\s+Law\s+Procedure",
    ]),
    ("juv.ct", "Ariz. R.P. Juv. Ct.", "Arizona Rules of Procedure for the Juvenile Court", [
        r"P\.?\s*Juv\.?\s*Ct\.?", r"of\s+Procedure\s+for\s+(?:the\s+)?Juvenile\s+Court",
    ]),
    ("spec.act", "Ariz. R.P. Spec. Act.", "Arizona Rules of Procedure for Special Actions", [
        r"P\.?\s*Spec\.?\s*Act(?:ions?)?\.?", r"of\s+Procedure\s+for\s+Special\s+Actions",
    ]),
    ("spec.act", "Ariz. R.P. Spec. Act.", "Arizona Rules of Procedure for Special Actions", [
        r"for\s+Special\s+Actions?",
    ]),
    # Missing from the first vocabulary pass; found only by reading the
    # anchors the extractor did NOT turn into cites (~35 per 3,000).
    ("prot.order.p", "Ariz. R. Protective Order P.", "Arizona Rules of Protective Order Procedure", [
        r"Protective\s+Ord(?:er|\.)\s*P(?:roc)?\.?", r"of\s+Protective\s+Order\s+Procedure",
    ]),
    ("sup.ct", "Ariz. R. Sup. Ct.", "Rules of the Supreme Court of Arizona", [
        r"Sup\.?\s*Ct\.?", r"of\s+the\s+Supreme\s+Court(?:\s+of\s+Arizona)?",
    ]),
    ("crim.p", "Ariz. R. Crim. P.", "Arizona Rules of Criminal Procedure", [
        r"Crim\.?\s*P(?:roc)?\.?", r"of\s+Criminal\s+Procedure",
    ]),
    ("civ.p", "Ariz. R. Civ. P.", "Arizona Rules of Civil Procedure", [
        r"Civ\.?\s*P(?:roc)?\.?", r"of\s+Civil\s+Procedure",
    ]),
    ("evid", "Ariz. R. Evid.", "Arizona Rules of Evidence", [
        r"Evid\.?", r"of\s+Evidence",
    ]),
]

RULE_SET_DISPLAY = {k: short for k, short, _l, _v in RULE_SETS}
RULE_SET_LONG = {k: lng for k, _s, lng, _v in RULE_SETS}
RULE_SET_DISPLAY["admin"] = "Ariz. Admin. Code"
RULE_SET_LONG["admin"] = "Arizona Administrative Code"

# "Ariz. R." / "Ariz.R." / "Arizona Rule(s)". The period or the full
# word is required, same reason as Minnesota's anchor: a bare "R" would
# match the start of any capitalized word after "Arizona".
_ANCHOR = r"\bAriz(?:ona)?\s*\.?\s*(?:R\.|Rules?\b)"

# A range ("Ariz. R. Crim. P. 26.1-26.3") is refused: never invent a
# cite to a range's first member.
_NUM = r"(\d{1,4}(?:\.\d{1,3})?)(?!\d|\.\d|\s?[-–]\s?\.?\d)"

# Up to four parenthetical levels -- (a)(7)(A) is common -- and never an
# edition year: `Ariz. R. Civ. P. 26.1 (2019)` is the rule's year, not a
# subsection (the bug Minnesota's admin rules had, found by reading
# output).
#
# Only the FIRST level may follow a space ("404 (b)"); deeper levels
# must be adjacent. Otherwise "31.10(a)(7)(A) (x)" swallows the next,
# unrelated parenthetical into the cite -- caught by a test.
_PAREN = r"\((?!(?:19|20)\d{2}\))[A-Za-z0-9.]{1,5}\)"
_TAIL = r"(?P<subsection>\s?" + _PAREN + r"(?:" + _PAREN + r"){0,3})?"

# A defined-term alias between the set and the number: Arizona Rule of
# Criminal Procedure (“Rule”) 33.1, ... (“ARCAP”) 21, ... (“ARFLP”) 83.
_ALIAS = r"(?:\s*\(\s*[“\"”']?\s*[A-Za-z]{2,6}\s*[“\"”']?\s*\))?"

_SET_PATTERNS = []
_REVERSE_PATTERNS = []
for _key, _s, _l, _variants in RULE_SETS:
    for _v in _variants:
        _SET_PATTERNS.append((_key, re.compile(
            _ANCHOR + r"\s*" + _v + _ALIAS + r"\s*" + _NUM + _TAIL, re.I)))
        # Number FIRST, set named right after: "Rule 18.4(b) of the
        # Arizona Rules of Criminal Procedure", "Rule 24, Ariz. R. Civ.
        # P., 16 A.R.S." -- the commonest miss in real output. Still a
        # full citation (the set is named), just in the other order.
        _REVERSE_PATTERNS.append((_key, re.compile(
            r"\bRule\s+" + _NUM + _TAIL
            + r"(?:\s*,\s*|\s+of\s+(?:the\s+)?)" + _ANCHOR + r"\s*" + _v,
            re.I)))
_REVERSE_PATTERNS.append(("civ.app.p", re.compile(
    r"\bRule\s+" + _NUM + _TAIL + r"\s*,\s*ARCAP\b")))

# ARCAP: the acronym IS the whole cite. Case-sensitive on purpose -- it
# is always written in capitals, and "Arcap" as a word is not a rule.
_ARCAP_RE = re.compile(r"\bARCAP\s*" + _NUM + _TAIL)

# Administrative Code: R<title>-<chapter>-<section>, e.g. A.A.C.
# R9-10-101, "Ariz. Admin. Code R2-19-119". A different body of law
# from court rules (agency rules), kept as its own set exactly as
# Minnesota's administrative rules are.
_ADMIN_RE = re.compile(
    r"(?:\bA\.\s?A\.\s?C\.|\bAriz(?:ona)?\.?\s*Admin(?:istrative)?\.?\s*Code)"
    r"\s*(?:§\s*)?(R\s?\d{1,2}-\d{1,2}-\d{1,4}(?:\.\d{1,2})?)" + _TAIL)


# THE MEMORANDUM-DECISION NOTICE. Every AZ memorandum decision opens:
#   NOTICE: NOT FOR OFFICIAL PUBLICATION. UNDER ARIZONA RULE OF THE
#   SUPREME COURT 111(c), THIS DECISION IS NOT PRECEDENTIAL ...
# A publication-status notice, not a court relying on the rule -- the
# Arizona twin of Minnesota's 136.01. Counted plainly it was ~89% of all
# Sup. Ct. rule cites in a 3,000-opinion sample.
#
# Measured 2026-10-16 on 6,000 opinions: 1,453 of 1,487 Rule 111 cites
# sit in the first 600 characters and the cue fires on 1,442 of them.
# The cue ALSO fires on 9 cites outside the head, and every one read by
# hand is genuine ("memorandum decisions 'are not precedential' and may
# not be cited ... See Ariz. R. Sup. Ct. 111(c)(1)(C)"). So unlike MN,
# where the cue alone was enough, the gate here is cue AND head. Rows are
# KEPT and flagged per occurrence, never excluded by number.
#
# A head 111(g) without the cue ("the Court of Appeals' opinion shall
# not be published, pursuant to Rule 111(g)") is the Supreme Court
# ordering depublication -- a real act, and it stays counted.
_NOTICE_CUE = re.compile(
    r"not\s+for\s+(?:official\s+)?publication|not\s+precedential"
    r"|does\s+not\s+create\s+legal\s+precedent|may\s+not\s+be\s+cited", re.I)
_NOTICE_HEAD = 600


def _is_boilerplate(text, start, rule_set, number):
    if rule_set != "sup.ct" or number != "111" or start >= _NOTICE_HEAD:
        return False
    return bool(_NOTICE_CUE.search(text[max(0, start - 200):start + 160]))


def _build(rule_set, number, m, start, text=""):
    sec = re.sub(r"\s+", "", m.groupdict().get("subsection") or "")
    number = re.sub(r"\s+", "", number)
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
    """Every Arizona rule citation in ``text``, one RuleRef per occurrence."""
    if not text:
        return []
    found = {}  # offset -> (match length, RuleRef); longest wins per start
    for rule_set, pattern in _SET_PATTERNS:
        for m in pattern.finditer(text):
            prev = found.get(m.start())
            if prev is None or len(m.group(0)) > prev[0]:
                found[m.start()] = (len(m.group(0)),
                                    _build(rule_set, m.group(1), m, m.start(), text))
    for rule_set, pattern in _REVERSE_PATTERNS:
        for m in pattern.finditer(text):
            prev = found.get(m.start())
            if prev is None or len(m.group(0)) > prev[0]:
                found[m.start()] = (len(m.group(0)),
                                    _build(rule_set, m.group(1), m, m.start(), text))
    for m in _ARCAP_RE.finditer(text):
        if m.start() not in found:
            found[m.start()] = (len(m.group(0)),
                                _build("civ.app.p", m.group(1), m, m.start(), text))
    for m in _ADMIN_RE.finditer(text):
        if m.start() not in found:
            found[m.start()] = (len(m.group(0)),
                                _build("admin", m.group(1), m, m.start(), text))
    return [ref for _n, ref in (found[k] for k in sorted(found))]


# Bare search query: "32.1", "Rule 60", "Rule 404(b)", "ARCAP 21".
# The dot is required UNLESS the reader typed "Rule"/"ARCAP": a bare
# "60" is a page number, but "Rule 60" says what it is. Which rule SET
# is decided against the corpus, and a collision is shown, never
# guessed -- Rule 60 exists in Civ. P., Crim. P. and Juv. Ct. alike.
BARE_RULE_RE = re.compile(
    r"^\s*(?:(?P<marker>rule|arcap)\s*)?(?P<number>\d{1,3}(?:\.\d{1,3})?)"
    r"(?P<tail>(?:\s?\([^)\s]{1,6}\)){0,4})\s*$",
    re.IGNORECASE,
)


def bare_rule_number(query):
    if not query:
        return None
    m = BARE_RULE_RE.match(query)
    if m is None:
        return None
    if "." not in m.group("number") and not m.group("marker"):
        return None
    body = m.group("number") + (m.group("tail") or "")
    return m.group("number"), body.replace(" ", "")


def bare_slug_candidates(rule_set, body):
    """Re-run ``extract`` over a synthesized cite so routing cannot drift
    from extraction. Arizona slugs stop at the rule number, so one
    candidate per set; the caller verifies it."""
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
