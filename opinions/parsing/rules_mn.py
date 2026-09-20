# -*- coding: utf-8 -*-
"""Minnesota court-rule (and administrative-rule) citation extractor.

A court rule is NOT a statute. `109.02` is Minn. R. Civ. App. P. 109.02
(in forma pauperis on appeal), not Minn. Stat. 109.02 -- and the whole
reason this module exists is that a researcher searching `109.02` was
being handed statute-shaped noise. Rules get their own table, their own
slug namespace and their own page label, for the same reason extraction
is never called "summarizing": a record labeled as the wrong kind of
thing is a wrong answer, not a partial one.

EVERY choice below is frequency-ranked from real prod text (1,200 recent
MN opinions, 4,148 full-form cites), not from a guess about what courts
write. Two findings that only appeared by reading raw output:

  * `Minn. R. Evid.` is a TOP-4 rule set (557 cites) and was missing
    from the vocabulary list this module was planned from.
  * The spelled-out long form ("Minnesota Rules of Civil Procedure
    12.02") is ~11% of all cites. The statute extractor excluded its own
    long form on an unmeasured "rare in appellate prose" assumption and
    capped the statute graph for months. Not repeating that.

TWO CLASSES OF RULE, deliberately distinguished:

  * COURT rules -- Minn. R. Civ. P., Crim. P., Evid., ... Promulgated by
    the supreme court, numbered `NN.NN`, subdivided with `subd.`
  * ADMINISTRATIVE rules -- Minn. R. 3310.2921 (unemployment hearings),
    8210.0600 (absentee ballots), 4410 (environmental review). Agency
    rules, numbered `NNNN.NNNN`, subdivided with `subp.` (subpart).

They share the "Minn. R." abbreviation and NOTHING else. Rendering an
agency rule under "Minnesota Rules of Civil Procedure" would misstate
the source of law, so `rule_set` records which body a cite belongs to
and the display label is derived from it, never reconstructed.
"""
import re
from collections import namedtuple

RuleRef = namedtuple(
    "RuleRef",
    "reference_slug reference_display rule_set rule_number subdivision "
    "subsection is_boilerplate text_offset",
)

SLUG_ROOT = "minn.r."

# Canonical set key -> (display label, ordered spelling variants).
# Variants are matched longest-first, so "Civ. App. P." wins over
# "Civ. P." on the same text. Counts are measured occurrences.
RULE_SETS = [
    ("civ.app.p", "Minn. R. Civ. App. P.", "Minnesota Rules of Civil Appellate Procedure", [
        r"Civ\.?\s*App\.?\s*P\.?", r"of\s+Civil\s+Appellate\s+Procedure",
        r"Civ\.?\s*App\.?\s*Proc\.?", r"App\.?\s*P\.",
    ]),
    ("juv.prot.p", "Minn. R. Juv. Prot. P.", "Minnesota Rules of Juvenile Protection Procedure", [
        r"Juv\.?\s*Prot\.?\s*P(?:roc)?\.?", r"of\s+Juvenile\s+Protection\s+Procedure",
    ]),
    ("juv.delinq.p", "Minn. R. Juv. Delinq. P.", "Minnesota Rules of Juvenile Delinquency Procedure", [
        r"Juv\.?\s*Delinq\.?\s*P(?:roc)?\.?", r"of\s+Juvenile\s+Delinquency\s+Procedure",
    ]),
    ("prof.conduct", "Minn. R. Prof. Conduct", "Minnesota Rules of Professional Conduct", [
        r"Prof\.?\s*Conduct", r"of\s+Professional\s+Conduct",
    ]),
    ("jud.conduct", "Minn. R. Jud. Conduct", "Minnesota Code of Judicial Conduct", [
        r"Jud\.?\s*Conduct", r"of\s+Judicial\s+Conduct",
    ]),
    ("gen.prac", "Minn. R. Gen. Prac.", "Minnesota General Rules of Practice", [
        r"Gen\.?\s*Pract?\.?", r"of\s+General\s+Practice",
    ]),
    ("crim.p", "Minn. R. Crim. P.", "Minnesota Rules of Criminal Procedure", [
        r"Crim\.?\s*P\.?", r"of\s+Criminal\s+Procedure", r"Crim\.",
    ]),
    ("civ.p", "Minn. R. Civ. P.", "Minnesota Rules of Civil Procedure", [
        r"Civ\.?\s*P\.?", r"of\s+Civil\s+Procedure",
    ]),
    ("evid", "Minn. R. Evid.", "Minnesota Rules of Evidence", [
        r"Evid\.?", r"of\s+Evidence",
    ]),
]

RULE_SET_DISPLAY = {k: short for k, short, _long, _v in RULE_SETS}
RULE_SET_LONG = {k: lng for k, _short, lng, _v in RULE_SETS}
RULE_SET_DISPLAY["admin"] = "Minn. R."
RULE_SET_LONG["admin"] = "Minnesota Administrative Rules"

# "Minn. R." / "Minn.R." / "Minnesota Rules". The trailing period or the
# full word is REQUIRED: without it the anchor matches the R of
# "Minnesota Real Estate" and "Minnesota River in townships", both of
# which showed up in the loose probe.
_ANCHOR = r"\bMinn(?:esota)?\s*\.?\s*(?:R\.|Rules?\b)"

# Court-rule number: 136.01, 60.02, 404, 8.4, 1.15. Administrative rule:
# 3310.2921 -- four digits, a dot, four digits. The shapes do not
# overlap, which is what makes the two bodies separable at all.
_COURT_NUM = r"(\d{1,3}(?:\.\d{1,3})?)"
_ADMIN_NUM = r"(\d{4}\.\d{4})"

# subd. 1 / subdivision 1 / subp. 2 (administrative), then an optional
# (c) subsection. Separate groups: the slug rolls up on subdivision the
# way the statute layer does, while the subsection stays display-only.
#
# SINGULAR ONLY, and that is the range guard -- same mechanism
# statutes_mn.py uses. A court writes the PLURAL exactly when it is
# citing a range ("subds. 2-3", "subps. 1a-1b", both in real text), so
# refusing to match the plural means a range can never bind a
# subdivision and we can never invent a cite to its first member. We
# cannot store a range; capturing "2" from "subds. 2-3" would be a
# wrong answer rather than a missing one.
#
# The subsection group captures the WHOLE parenthetical chain, parens
# included: the court that wrote `Minn. R. Evid. 103(a)(2)` did not
# write `103(a)`, and keeping only the first group would point at a
# broader provision than the one cited. That is a misstated citation,
# the same class as rendering `A.R.S. 13-1103` as "section 13.1103".
#
# The year lookahead is load-bearing and was found by reading real
# output, not by a test: `Minn. R. 3310.2912 (2025)` was storing 2025
# as a subsection. `(2025)` is the rule's EDITION YEAR, exactly as
# statutes carry `(2024)`. Rejecting only 19xx/20xx leaves a genuine
# 4-digit subsection matchable, if one ever exists.
_TAIL = (
    r"(?:\s*,?\s*(?:subd\.|subdivision|subp\.|subpart)\s*"
    r"(?P<subdivision>\d+[a-zA-Z]?))?"
    r"(?P<subsection>(?:\((?!(?:19|20)\d{2}\))[^)\s]{1,6}\)){1,2})?"
)

_SET_PATTERNS = []
for _key, _short, _long, _variants in RULE_SETS:
    for _v in _variants:
        _SET_PATTERNS.append((_key, re.compile(
            _ANCHOR + r"\s*" + _v + r"\s*" + _COURT_NUM + _TAIL, re.I)))

_ADMIN_RE = re.compile(_ANCHOR + r"\s*" + _ADMIN_NUM + _TAIL, re.I)

# The nonprecedential disclaimer cites Minn. R. Civ. App. P. 136.01,
# subd. 1(c) at the TOP of every unpublished MN opinion. Measured: 1,284
# of 4,148 cites (31%) are this one string, 85% of them in the first 400
# characters, and 99% of the opinions carrying it are nonprecedential --
# while every other rule cite is spread flat through the document.
#
# It is a publication-status notice, not the court engaging with a rule.
# Counted as a citation it would make 136.01 the most-cited rule in
# Minnesota by an order of magnitude, which is the "accordingly, we"
# trap from the holdings extractor in a new costume: frequency is not
# significance.
#
# But 123 occurrences sit OUTSIDE the head and are real cites, so this
# is flagged PER OCCURRENCE on text evidence -- never dropped, and never
# blanket-excluded by rule number.
_BOILERPLATE_CUE = re.compile(
    r"nonprecedential|not\s+be\s+cited|will\s+be\s+unpublished", re.I)
_BOILERPLATE_RULE = ("civ.app.p", "136.01")


def _is_boilerplate(text, start, rule_set, number):
    """True when this occurrence is the unpublished-opinion disclaimer."""
    if (rule_set, number) != _BOILERPLATE_RULE:
        return False
    window = text[max(0, start - 260):start]
    return bool(_BOILERPLATE_CUE.search(window))


def _build(rule_set, number, m, text):
    # Ranges are excluded by the grammar itself (see _TAIL): the plural
    # marker never matches, so a range arrives here with no subdivision.
    sub = (m.groupdict().get("subdivision") or "").strip()
    sec = (m.groupdict().get("subsection") or "").strip()

    slug = SLUG_ROOT + rule_set + "." + number
    if sub:
        slug += ".subd." + sub

    display = RULE_SET_DISPLAY[rule_set] + " " + number
    if sub:
        display += ", subd. " + sub if rule_set != "admin" else ", subp. " + sub
    if sec:
        display += sec          # already parenthesized, e.g. "(a)(2)"

    return RuleRef(
        reference_slug=slug.lower(),
        reference_display=display,
        rule_set=rule_set,
        rule_number=number,
        subdivision=sub,
        subsection=sec,
        is_boilerplate=_is_boilerplate(text, m.start(), rule_set, number),
        text_offset=m.start(),
    )


def extract(text):
    """Return every rule citation in ``text`` as a list of RuleRef.

    One row per OCCURRENCE (like StatuteCitation) -- query-time DISTINCT
    collapses to one row per opinion when that is what is wanted.
    """
    if not text:
        return []

    found = {}  # offset -> RuleRef; longest-match-wins per start position
    for rule_set, pattern in _SET_PATTERNS:
        for m in pattern.finditer(text):
            prev = found.get(m.start())
            if prev is None or len(m.group(0)) > prev[0]:
                found[m.start()] = (len(m.group(0)),
                                    _build(rule_set, m.group(1), m, text))

    for m in _ADMIN_RE.finditer(text):
        if m.start() not in found:
            found[m.start()] = (len(m.group(0)),
                                _build("admin", m.group(1), m, text))

    return [ref for _len, ref in
            (found[k] for k in sorted(found))]


def rule_set_label(rule_set, long_form=False):
    """Display label for a rule set. NEVER reconstruct a cite by hand.

    The statute page rebuilt AZ citations with Minnesota's grammar and
    printed `A.R.S. 13-1103` as "section 13.1103" -- a misstated
    citation. Labels come from this map or they do not get printed.
    """
    table = RULE_SET_LONG if long_form else RULE_SET_DISPLAY
    return table.get(rule_set, "")
