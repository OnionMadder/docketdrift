"""Minnesota appellate opinion parser.

Handles both the Minnesota Supreme Court and the Minnesota Court of
Appeals -- they share most layout conventions. The parser is designed
to fail open: any field it can't extract with reasonable confidence is
returned as ``None``, so the caller leaves the corresponding Opinion
field unset and a human can fill it in via admin.

Reference for the format: a typical Minnesota Court of Appeals opinion
opens with::

    STATE OF MINNESOTA
    IN COURT OF APPEALS
    A25-1257

    In the Matter of the Application of
    Anthony Allen Jessie Garnett for a Change of Name.

    Filed June 1, 2026
    Reversed and remanded
    Larson, Judge

The Minnesota Supreme Court layout is nearly identical, with
``SUPREME COURT`` in the header and ``Justice`` in the byline.
Unpublished COA opinions carry the footer "This opinion is
nonprecedential except as provided by Minn. R. Civ. App. P. 136.01."
"""
from __future__ import annotations

import re
from datetime import datetime

from .base import ParsedOpinion, StateParser


# ---------- Patterns -----------------------------------------------------

# Case number: COA cases use ``A##-####``; SCt admin docket uses
# ``ADM##-####``; older numbering occasionally appears as ``C#-##-####``.
CASE_NUMBER_RE = re.compile(
    r"\b(A\d{2}-\d{4}|ADM\d{2}-\d{4}|C\d{1,2}-\d{2}-\d{4})\b"
)

# Court level from the header. Captured group is "COURT OF APPEALS"
# or "SUPREME COURT" -- not stored on Opinion (Opinion.court is the FK)
# but used as a confidence hint that we're looking at the right document.
COURT_LEVEL_RE = re.compile(
    r"IN\s+(?:THE\s+)?(COURT\s+OF\s+APPEALS|SUPREME\s+COURT)",
    re.IGNORECASE,
)

# Filing date line: ``Filed June 1, 2026`` or ``Filed Jan. 14, 2026``.
# The colon is REQUIRED to be optional: the Court of Appeals writes
# "Filed <date>", but the Supreme Court writes "Filed:  <date>" (colon, two
# spaces). Without `:?` every Supreme PDF fails with "parser found no
# release_date" -- 234 of 240 did, on the first Supreme PDF ingest ever
# attempted. It went unnoticed because Supreme opinions had only ever arrived
# pre-parsed from CourtListener's bulk CSV; this parser was written against
# COA documents.
FILED_DATE_RE = re.compile(r"Filed:?\s+([A-Za-z\.]+\s+\d{1,2},?\s+\d{4})")

# Order opinions ("ORDER OPINION") don't carry a "Filed [date]" line -- they
# date themselves at the foot: ``Dated: January 6, 2026 BY THE COURT``. Used as
# a fallback when FILED_DATE_RE misses so this whole class (HRO appeals, other
# procedural dispositions -- absent from CourtListener) can be ingested.
DATED_DATE_RE = re.compile(r"Dated:\s*([A-Za-z\.]+\s+\d{1,2},?\s+\d{4})")

# Header that closes the caption block in an order opinion (stands in for the
# "Filed [date]" boundary regular opinions use).
ORDER_OPINION_HDR_RE = re.compile(r"\bORDER\s+OPINION\b")

# "SPECIAL TERM ORDER"s are a second order-opinion layout with a caption-FIRST
# structure: parties right under "IN COURT OF APPEALS", then this header, THEN
# the case number -- the reverse of a regular opinion (case number, caption,
# "Filed"). Match either order-header phrasing to bound that caption block.
# No trailing \b: a footnote superscript often fuses to the header ("ORDER1"),
# and \b between "R" and "1" would fail to match.
ORDER_HDR_RE = re.compile(
    r"\b(?:SPECIAL\s+TERM\s+ORDER|ORDER\s+OPINION)", re.IGNORECASE)

# Party-role words trailing each side of an order caption ("<Name>, Respondent,
# vs. <Name>, Appellant.") -- stripped to leave just the party names.
# "Respondent(s) Below" (a party below who is not a party to the appeal) is ONE
# role: stripping only ", Respondent" left a stray "Below" glued to the party
# name ("Eric Joseph Vacko Below, Brittany Ann Vacko") on 38 MN titles,
# found via a removal request 2026-10-24.
# pypdf sometimes splits a role word once ("Appella nts"), so one optional
# space is allowed between letters.
def _split_tolerant(word: str) -> str:
    return " ?".join(re.escape(ch) for ch in word)


_CAPTION_ROLE_RE = re.compile(
    r",\s*(?:" + "|".join(_split_tolerant(w) + "s?" for w in (
        "Respondent", "Appellant", "Petitioner", "Relator", "Cross-Appellant",
        "Cross-Respondent", "Plaintiff", "Defendant")) + r")"
    r"(?:\s+Below)?\b\.?",
    re.IGNORECASE)


_JUDGE_BLOCK_RE = re.compile(
    r"(,\s*(?:C\.)?J\.|Concurring|Dissenting|Took no part|"
    r"^\s*Court of Appeals\b|Office of Appellate Courts)",
    re.IGNORECASE | re.MULTILINE,
)


_ROLE_ONLY_RE = re.compile(
    r"^\s*(?:appellants?|respondents?|petitioners?|relators?|"
    r"cross-appellants?|cross-respondents?|appellants?/cross-respondents?|"
    r"respondents?/cross-appellants?|et\s+al\.?)(?:\s+below)?\s*[,.]?\s*$",
    re.IGNORECASE,
)


def _looks_like_role_label(par: str) -> bool:
    """True for a caption fragment that is only a party ROLE, no name.

    Captions wrap as `Ryan Larson,` / `Appellant,` across separate blocks, so
    a block can be all role and no party -- titling the opinion
    "Respondent/Cross-Appellant,". Those are never the case name.
    """
    return bool(_ROLE_ONLY_RE.match(" ".join(par.split())))


def _looks_like_judge_block(par: str) -> bool:
    """True when a caption-candidate paragraph is really the panel block.

    Supreme Court opinions print the authoring justice and any separate
    writings between the docket number and the party caption, so the first
    paragraph after the docket number is the panel, not the case name.
    Party captions do carry commas, but not ", J." / ", C.J." tokens.
    """
    return bool(_JUDGE_BLOCK_RE.search(par))


# Trailing tokens whose period belongs to the NAME, not to the caption's
# closing punctuation: "The Emily Program, P.C." / "Acme Holdings, Inc."
# Blind .strip(" ,.") took those periods off -- the same defect class as the
# holdings extractor splitting "rule 24.03" at "24." (see CLAUDE.md). A
# party's name is part of the record; trimming a character off it is a
# misquote, however small.
_ABBREV_TAIL_RE = re.compile(
    r"(?:[A-Za-z]\.(?:[A-Za-z]\.)+"          # P.C., L.L.C., U.S.A.
    r"|(?<![A-Za-z])[A-Z]\."                 # an initial: "T. G. G."
    r"|\b(?:Inc|Corp|Co|Ltd|LLC|LLP|PLLC|PA|Assn|Bros|Jr|Sr|No|al)\.)$",
    re.IGNORECASE,
)


def _strip_caption_tail(s: str) -> str:
    """Trim a caption fragment's punctuation, sparing abbreviations."""
    s = s.strip().strip(",").strip()
    if s.endswith(".") and not _ABBREV_TAIL_RE.search(s):
        s = s[:-1].strip().rstrip(",").strip()
    return s


# A caption region can open with the docket number itself (consolidated
# appeals repeat it), which is OUR boundary artifact, not part of the case
# name. Strip a LEADING docket token only -- parenthesized dockets inside the
# caption are the court's own text and stay.
_LEADING_DOCKET_RE = re.compile(r"^[,\s]*A\d{2}-\d{3,4}\b[,\s]*", re.IGNORECASE)

# "Smith, vs" -- the split found a "vs" but the opposing party never made it
# into the caption region. That is a FRAGMENT, not a name. Returning it would
# publish half a caption as if it were the case; returning nothing leaves the
# stored title alone, which is the honest failure.
_DANGLING_VS_RE = re.compile(r"[,\s]+vs?\.?$", re.IGNORECASE)


def _order_caption_name(caption: str) -> str:
    """Turn an order-opinion caption block into a clean case name.

    The block reads "<Party1>, Respondent, vs. <Party2>, Appellant." with the
    parties split across lines. Join it, split on vs./v., strip the trailing
    role words, and rejoin as "Party1 v. Party2". Captions with no "vs." (e.g.
    "In re the Marriage of ...") keep their text, roles removed.
    """
    flat = _LEADING_DOCKET_RE.sub("", " ".join(caption.split()))
    if _DANGLING_VS_RE.search(flat):
        return ""
    sides = re.split(r"\bvs?\.\s*", flat, maxsplit=1)
    if len(sides) == 2:
        left = _strip_caption_tail(_CAPTION_ROLE_RE.sub("", sides[0]))
        right = _strip_caption_tail(_CAPTION_ROLE_RE.sub("", sides[1]))
        if left and right:
            return f"{left} v. {right}"
    return _strip_caption_tail(_CAPTION_ROLE_RE.sub("", flat))


# ---------- Supreme Court two-column caption -----------------------------
#
# A Supreme opinion prints its caption in a LEFT column while the right column
# carries the author, separate writings, "Filed: <date>" and "Office of
# Appellate Courts" -- interleaved line by line:
#
#     A15-2052
#     Court of Appeals                                Hudson, J.
#                              Dissenting, Gildea, C.J., and Anderson, J.
#     Joseph W. Frederick,
#                    Appellant,
#     vs.                                  Filed:  February 7, 2018
#                                          Office of Appellate Courts
#     Kay L. Wallerich, et al.,
#                    Respondents.
#     ________________________
#
# The regular path ends the caption at "Filed", which sits MID-caption here, so
# ~330 Supreme PDFs (2017-2026) were titled "State of Minnesota,", "Tyrone
# James White, Appellant, vs.", a companion docket, or nothing (73). Read to
# the rule line instead and strip the right column.
_SUP_RULE_RE = re.compile(r"^\s*_{5,}\s*$", re.MULTILINE)
_SUP_STOP_RE = re.compile(
    r"S\s*Y\s*L\s*L\s*A\s*B\s*U\s*S|\bfor\s+(?:appellant|respondent|relator|petitioner)s?\b",
    re.IGNORECASE)
_COUNSEL_PARA_RE = re.compile(
    r",\s*(?:Minnesota|MN|Wisconsin|Iowa|D\.\s*C\.)\s*(?:;|,\s*for\b)"
    r"|\bfor\s+(?:appellant|respondent|relator|petitioner|amic)",
    re.IGNORECASE)
_SUP_RIGHT_COL_RES = (
    re.compile(r"Filed:?\s+[A-Za-z\.]+\s+\d{1,2},?\s+\d{4}"),
    re.compile(r"Office\s+o\s?f\s+(?:the\s+)?Appellate\s+Courts", re.IGNORECASE),
    re.compile(r"\bPer\s+Curiam\b"),
)
# A separate-writing credit: "Dissenting, Gildea, C.J., and Anderson, J." /
# "Took no part, Stras, J." / "Concurring in part, dissenting in part,". The
# credited judges FOLLOW the label (and may wrap to the next line), and the
# left-column party, if any, comes BEFORE it -- so a line keeps only the text
# ahead of the label.
_SUP_LABEL_RE = re.compile(
    r"\b(?:Concurring|Dissenting|Took\s+no\s+part)\b", re.IGNORECASE)
# Anything of the right column that survived means the layout was not what
# this reader expects. Returning "" leaves the stored title alone, rather than
# swapping one wrong title for another.
_SUP_RESIDUE_RE = re.compile(
    r"Concurring|Dissenting|Took\s+no\s+part|Office\s+o\s?f\s+(?:the\s+)?Appellate"
    # (checked AFTER tail trimming, so "J." / "JJ." may have lost the period)
    r"|Court\s+o\s?f\s+A\s?ppeals\s+[A-Z][a-z]+,|,\s*(?:C\.\s*)?J\.?(?=,|\s+v\.|\s|$)|\bJJ\b",
    re.IGNORECASE)
# Separate-writing credits WRAP onto the party's line ("Dissenting, McKeig," /
# "State of Minnesota, Chutich, and Thissen, JJ."), where "Minnesota," and
# "Chutich," are textually alike. The court's bench is a small closed set, so
# strip a trailing run of JUSTICE surnames -- only when it ends in a judicial
# title, so a party named "Anderson," is never touched.
_MN_JUSTICES = (
    "Gildea", "Anderson", "Hudson", "Lillehaug", "Chutich", "Thissen", "McKeig",
    "Moore", "Hennesy", "Procaccini", "Gaïtas", "Gaitas", "Stras", "Wright",
    "Dietzen", "Page", "Meyer", "Magnuson", "Blatz", "Hanson", "Gilbert",
    "Stringer", "Lancaster", "Tomljanovich", "Keith", "Coyne", "Simonett",
    "Kelley", "Yetka", "Wahl", "Popovich",
)
_JUSTICE_NAME = (r"(?:" + "|".join(_MN_JUSTICES) + r")(?:,\s*(?:III|II|Jr\.?))?")
_SUP_TRAILING_JUDGE_RE = re.compile(
    r"(?:(?:^|,?\s+)(?:and\s+)?" + _JUSTICE_NAME + r")+,?\s*"
    r"(?:C\.\s*J\.?|J\.|JJ\.),?\s*$")
# ...and a credit at the START of a line ("Anderson, J, Barbara Henry/...").
_SUP_LEADING_JUDGE_RE = re.compile(
    r"^\s*(?:(?:and\s+)?" + _JUSTICE_NAME + r",?\s*)+(?:C\.\s*J\.?|JJ\.?|J\.?)(?=[,\s]|$)[,\s]*")
_COUNTY_LABEL = r"(?:St\.\s+)?[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?\s+County"
_BELOW_LABEL = (r"(?:Court\s+o\s?f\s+A\s?ppeals|Tax\s+Court|Workers.{0,3}\s+Compensation"
                r"(?:\s+Court\s+of\s+Appeals)?|Original\s+Jurisdiction(?:\s+Per\s+Curiam)?"
                r"|Per\s+Curiam)")
_SUP_LABEL_CREDIT_LINE_RE = re.compile(
    r"^\s*(?:" + _BELOW_LABEL + r"|" + _COUNTY_LABEL + r")\b"
    r".*,\s*(?:C\.\s*J\.?|J\.|JJ\.),?\s*$")
_SUP_BARE_ROLE_RE = re.compile(
    r"([^\s,])\s+((?:Cross-)?(?:Appellants?|Respondents?|Relators?|Petitioners?)[.,])")
# Lower-court labels no party is ever named.
_SUP_BELOW_LABEL_RE = re.compile(r"^\s*" + _BELOW_LABEL + r"[\s,]*$")
# A county label can ALSO be a party ("Mower County,"), so it is dropped only
# before the first party line.
_SUP_COUNTY_LABEL_RE = re.compile(r"^\s*" + _COUNTY_LABEL + r"[\s,]*$")
_DOCKET_LINE_RE = re.compile(r"^\s*(?:A|ADM)\d{2}-\d{3,4}\s*$")
# pypdf letter-spacing: "A p p e l l a n t s ," -> "Appellants,"
_LETTER_SPACED_RE = re.compile(r"\b(?:[A-Za-z] ){3,}[A-Za-z]\b")


def _unspace_letters(s: str) -> str:
    return _LETTER_SPACED_RE.sub(lambda m: m.group(0).replace(" ", ""), s)


def _supreme_caption(raw_text: str, case_number: str) -> str:
    """Case name from a Supreme opinion's two-column caption, or ""."""
    start = raw_text.find(case_number)
    if start < 0:
        return ""
    region = raw_text[start + len(case_number):start + len(case_number) + 3000]
    m_end = _SUP_RULE_RE.search(region)
    if m_end:
        end = m_end.start()
    else:
        # No rule line: stop at the counsel block, cut back to the blank line
        # before it -- otherwise the lawyers' names join the caption.
        m_stop = _SUP_STOP_RE.search(region)
        if not m_stop:
            return ""
        end = m_stop.start()
        # Counsel can run several paragraphs ("..., Minnesota; and"): end at
        # the FIRST paragraph that reads like counsel.
        pos = 0
        for gap in re.finditer(r"\n[ \t\xa0]*\n", region[:m_stop.start()]):
            para = region[pos:gap.start()]
            if pos and _COUNSEL_PARA_RE.search(para):
                end = pos
                break
            pos = gap.end()
        else:
            if pos and _COUNSEL_PARA_RE.search(region[pos:m_stop.end()]):
                end = pos
    lines = []
    for line in region[:end].splitlines():
        line = _unspace_letters(line)
        # pypdf's space-before-punctuation ("McKeig , J .") would hide the
        # judge credit from every pattern below.
        line = re.sub(r"\s+([,.])", r"\1", line)
        for rx in _SUP_RIGHT_COL_RES:
            line = rx.sub(" ", line)
        if _SUP_LABEL_CREDIT_LINE_RE.match(line):
            continue
        m_label = _SUP_LABEL_RE.search(line)
        if m_label:
            line = line[:m_label.start()]
        # The author shares a line with the left-column party ("State of
        # Minnesota,      McKeig, J."): strip the credit, keep the party.
        prev = None
        while prev != line:
            prev = line
            # Keep the comma that separated the party from the credit.
            line = _SUP_TRAILING_JUDGE_RE.sub(",", line)
        line = _SUP_LEADING_JUDGE_RE.sub("", line)
        if _DOCKET_LINE_RE.match(line) or _SUP_BELOW_LABEL_RE.match(line):
            continue
        if not lines and _SUP_COUNTY_LABEL_RE.match(line):
            continue
        line = " ".join(line.split()).strip()
        if line.strip(" ,"):
            lines.append(line)
    joined = re.sub(r",(?:\s*,)+", ",", " ".join(lines))
    # A role that lost its comma ("City of Circle Pines / Appellant,") --
    # checked on the joined text, since the role sits on its own line.
    joined = _SUP_BARE_ROLE_RE.sub(r"\1, \2", joined)
    name = _order_caption_name(joined)
    # Only a recognizable caption -- an empty or one-sided result means the
    # layout was not what this reader expects; the caller falls back.
    if not re.search(r"\sv\.\s|^In\s+(?:re|the)\b", name, re.IGNORECASE):
        return ""
    if _SUP_RESIDUE_RE.search(name) or not re.match(r"[A-Z0-9]", name):
        return ""
    # A side that is only a role word ("Respondent v. Omar Nur Hassan") means
    # the party's line was lost.
    if re.match(r"(?:Respondents?|Appellants?|Relators?|Petitioners?)\b", name):
        return ""
    return name if 4 <= len(name) <= 400 else ""


# Footer marker that flips is_precedential to False. Regular opinions say "This
# opinion ... is nonprecedential"; order opinions say "this order opinion is
# nonprecedential" OR just "this order is nonprecedential" (per Minn. R. Civ.
# App. P. 136.01, subd. 1(c)).
#
# BEFORE that wording, unpublished opinions opened "This opinion will be
# unpublished and may not be cited except as provided by Minn. Stat.
# § 480A.08, subd. 3." Matching only the newer "is nonprecedential" form
# defaulted every older unpublished PDF to precedential=True: 4,072 MN
# opinions from the 2017-2020 archive backfill (measured 2026-10-24).
NONPRECEDENTIAL_RE = re.compile(
    r"This\s+(?:order(?:\s+opinion)?|opinion)\s+"
    r"(?:(?:will\s+be\s+unpublished\s+and\s+)?is\s+nonprecedential"
    r"|will\s+be\s+unpublished\s+and\s+may\s+not\s+be\s+cited)",
    re.IGNORECASE,
)

# Disposition phrases. Order matters: longer / more specific phrases must
# come first because the regex returns the leftmost match.
_DISPOSITION_PATTERNS = (
    r"Affirmed\s+in\s+part,\s+reversed\s+in\s+part,\s+and\s+remanded",
    r"Affirmed\s+in\s+part\s+and\s+reversed\s+in\s+part",
    r"Affirmed\s+in\s+part",
    r"Reversed\s+and\s+remanded",
    r"Vacated\s+and\s+remanded",
    r"Modified\s+and\s+affirmed",
    r"Reversed",
    r"Affirmed",
    r"Remanded",
    r"Vacated",
    r"Modified",
    r"Dismissed",
    r"Reinstated",
    r"Stayed",
    r"Granted",
    r"Denied",
)
DISPOSITION_RE = re.compile(
    r"\b(" + "|".join(_DISPOSITION_PATTERNS) + r")\b",
    re.IGNORECASE,
)

# Judge byline -- a line like ``Larson, Judge`` after the disposition.
# We require the comma + role suffix to avoid catching unrelated
# capitalized phrases. Allows hyphenated and apostrophe'd surnames.
#
# Whitespace between name words is `[ \t]+` (horizontal only) so the
# regex doesn't slurp the preceding "Affirmed" line into the captured
# name. `\s+` would match newlines and a multi-line byline page like
#     Filed February 2, 2015
#                 Affirmed
#               Smith, Judge
# would greedily capture "Affirmed\n... Smith" as a 2-word name.
# Supreme Court captions put the author top-right on the SAME line as the
# court below ("Court of Appeals        Gaïtas, J." / "Hennepin County
# Hudson, C.J."), so the optional prefix is a court/county name followed
# by a wide gap -- tight enough that a sentence-internal "X, Judge;" list
# cannot match. Name class allows Latin-1 letters: Justice Gaïtas had 7
# votes in two years because "ï" fell outside [A-Za-z] (2026-09-26).
JUDGE_BYLINE_RE = re.compile(
    r"^[ \t]*(?:[A-Z][A-Za-z' .]*?(?:Court|County|Appeals|Board|Commission)[ \t]{2,})?"
    r"([A-ZÀ-ß][A-Za-zÀ-ÿ'\-]+(?:[ \t]+[A-ZÀ-ß][A-Za-zÀ-ÿ'\-]+){0,3}),[ \t]*"
    r"(Chief[ \t]+Justice|Justice|Chief[ \t]+Judge|Presiding[ \t]+Judge|Judge|J\.|C\.J\.)\.?[ \t]*$",
    re.MULTILINE,
)

# Panel intro: ``Considered and decided by [judges].``. We split the
# trailing blob into per-judge chunks on semicolons (MN's canonical
# separator), optionally preceded by " and " before the last name:
#     "Smith, Presiding Judge; Ross, Judge; and Schellhas, Judge"
# A previous version also split on "<NAME>, <Title>" via a
# `,\s+(?=[A-Z][a-z])` lookahead -- that was eager and split off
# "Smith" from "Smith, Presiding Judge", losing real panel members and
# fabricating "Presiding Judge" as a separate "name". Semicolons are
# the reliable separator; comma is the name/role delimiter within one
# panel member, not between members.
PANEL_INTRO_RE = re.compile(
    r"Considered\s+and\s+decided\s+by\s+(.+?)(?:\.\s|$)",
    re.IGNORECASE | re.DOTALL,
)

# Statute citation, e.g. ``Minn. Stat. § 259.10, subd. 1``.
STATUTE_RE = re.compile(
    r"Minn\.?\s*Stat\.?\s*§?\s*(\d+[A-Z]?\.\d+(?:\(\d+\))?(?:,\s*subd\.\s*\d+[a-z]?)?)"
)

# Case-name citation, e.g. ``State v. Smith, 123 Minn. 456 (2020)``.
# Heuristic -- we collect raw strings without parsing them into a
# structured citation. v1 is good enough for the "what does this opinion
# cite" view; a proper citator can come later.
CITATION_RE = re.compile(
    r"([A-Z][A-Za-z\.\s']+?\s+v\.\s+[A-Z][A-Za-z\.\s']+?,\s+"
    r"\d+\s+[A-Z][\w\.\s]*?\s+\d+(?:,\s+\d+)?\s+\(\d{4}\))"
)


# ---------- Parser -------------------------------------------------------

class MinnesotaParser(StateParser):
    state_code = "MN"
    version = "v1"

    def parse(self, raw_text: str) -> ParsedOpinion:
        result = ParsedOpinion()
        if not raw_text:
            return result

        # Header confidence -- did we see "IN COURT OF APPEALS" or
        # "IN SUPREME COURT"? Affects how much we trust the implicit
        # "precedential" inference (the absence of the nonprecedential
        # footer doesn't mean much if this isn't even an MN opinion).
        header_conf = 0.9 if COURT_LEVEL_RE.search(raw_text) else 0.4

        # --- Case number ---------------------------------------------------
        m = CASE_NUMBER_RE.search(raw_text)
        if m:
            result.case_number = m.group(1)
            result.confidence["case_number"] = 0.95

        # --- Release date --------------------------------------------------
        # Regular opinions: "Filed [date]". Order opinions: "Dated: [date]".
        m_filed = FILED_DATE_RE.search(raw_text)
        m_date = m_filed or DATED_DATE_RE.search(raw_text)
        if m_date:
            raw_date = m_date.group(1).replace(",", "").replace(".", "")
            for fmt in ("%B %d %Y", "%b %d %Y"):
                try:
                    result.release_date = datetime.strptime(raw_date, fmt).date()
                    result.confidence["release_date"] = 0.95 if m_filed else 0.9
                    break
                except ValueError:
                    continue

        # --- Precedential --------------------------------------------------
        if NONPRECEDENTIAL_RE.search(raw_text):
            result.is_precedential = False
            result.confidence["is_precedential"] = 0.95
        else:
            # Absence is a weaker positive signal -- only treat as
            # precedential when we also saw the MN header.
            result.is_precedential = True
            result.confidence["is_precedential"] = header_conf

        # --- Case name -----------------------------------------------------
        # The caption sits between the case number and the "Filed [date]" line
        # (regular opinions) or the "ORDER OPINION" header (order opinions).
        m_hdr = None if m_filed else ORDER_OPINION_HDR_RE.search(raw_text)
        boundary = m_filed.start() if m_filed else (m_hdr.start() if m_hdr else None)
        if result.case_number and re.search(
                r"IN\s+SUPREME\s+COURT", raw_text[:1500], re.IGNORECASE):
            name = _supreme_caption(raw_text, result.case_number)
            if name:
                result.case_name = name
                result.confidence["case_name"] = 0.75
        if result.case_number and boundary is not None and not result.case_name:
            after_num_idx = raw_text.find(result.case_number)
            if after_num_idx >= 0:
                between = raw_text[after_num_idx + len(result.case_number):boundary]
                paragraphs = [p.strip() for p in re.split(r"\n\s*\n", between) if p.strip()]
                if m_filed:
                    # Regular: the caption is the first paragraph block (it
                    # wraps across lines but stops at a blank line).
                    #
                    # EXCEPT on Supreme Court opinions, where the panel block
                    # sits between the docket number and the caption:
                    #     A17-0221
                    #     Court of Appeals   McKeig, J.
                    #       Concurring in part and dissenting in part,
                    #      Anderson, J., Gildea, C.J., Hudson, J.
                    #     Joel Jennissen, et al.,
                    # Taking paragraphs[0] there titles the opinion with its
                    # own panel ("Court of Appeals Chutich, J. Concurring..."),
                    # so skip blocks that are clearly judge lines.
                    caption_ps = [p for p in paragraphs
                                  if not _looks_like_judge_block(p)
                                  and not _looks_like_role_label(p)] or paragraphs
                    # JOIN every caption block, don't take only the first.
                    # A regular opinion's caption wraps across blank lines
                    # exactly like an order's does:
                    #     Joseph T. Green, Appellant,
                    #
                    #     vs.
                    #
                    #     State National Insurance Company, Inc., Respondents.
                    # Taking paragraphs[0] kept only the appellant and threw
                    # the opposing party away, titling 6,779 MN opinions
                    # (9.7%) with one side of the caption -- reported by a
                    # party who noticed his own page named him and not the
                    # State (2026-09-14). Normalizing through
                    # _order_caption_name gives "X v. Y", which is both the
                    # standard case-name form and what every other row in the
                    # corpus already looks like.
                    candidate = _order_caption_name(
                        " ".join(caption_ps)) if caption_ps else ""
                    conf = 0.7
                else:
                    # Order opinion: the caption is split across blank lines
                    # (one party per block -- "Name,", "Appellant,", "vs.",
                    # ...), so join every block into the full caption.
                    candidate = " ".join(" ".join(p.split()) for p in paragraphs)
                    conf = 0.6
                if 4 <= len(candidate) <= 400:
                    result.case_name = candidate
                    result.confidence["case_name"] = conf

        # Fallback for the "SPECIAL TERM ORDER" caption-FIRST layout: the case#
        # anchoring above finds nothing because the caption sits ABOVE the case
        # number (between "IN COURT OF APPEALS" and the order header). Pull it
        # from that region and normalize the parties to "X v. Y".
        if not result.case_name:
            court_hdr = COURT_LEVEL_RE.search(raw_text)
            order_hdr = ORDER_HDR_RE.search(raw_text)
            if court_hdr and order_hdr and court_hdr.end() < order_hdr.start():
                name = _order_caption_name(
                    raw_text[court_hdr.end():order_hdr.start()])
                if 4 <= len(name) <= 400:
                    result.case_name = name
                    result.confidence["case_name"] = 0.6

        # --- Disposition ---------------------------------------------------
        # Highest confidence when the disposition sits immediately after
        # the "Filed [date]" line; weaker confidence if we have to find
        # it anywhere in the document.
        if m_filed:
            window = raw_text[m_filed.end():m_filed.end() + 500]
            m_disp = DISPOSITION_RE.search(window)
            if m_disp:
                result.disposition = self._normalize_disposition(m_disp.group(1))
                result.confidence["disposition"] = 0.9
        if not result.disposition:
            m_disp = DISPOSITION_RE.search(raw_text)
            if m_disp:
                result.disposition = self._normalize_disposition(m_disp.group(1))
                result.confidence["disposition"] = 0.45

        # --- Author byline -------------------------------------------------
        bylines = JUDGE_BYLINE_RE.findall(raw_text)
        if bylines:
            last_name, role = bylines[0]
            result.author = f"{last_name}, {self._normalize_role(role)}"
            result.confidence["author"] = 0.85

        # --- Panel ---------------------------------------------------------
        m_panel = PANEL_INTRO_RE.search(raw_text)
        if m_panel:
            blob = m_panel.group(1)
            # The blob is something like:
            #   "Larson, Judge; Bjorkman, Judge; and Wheelock, Judge"
            #   "Smith, Presiding Judge; Ross, Judge; and Schellhas, Judge"
            # Split on semicolons only -- they're MN's reliable separator
            # between panel members. Each chunk is then "<surname>, <role>".
            # `(?:and\s+)?` consumes the "and " before the last name when
            # present.
            chunks = re.split(r"\s*;\s*(?:and\s+)?", blob)
            for chunk in chunks:
                m2 = re.match(
                    r"\s*([A-Z][A-Za-z'\-]+(?:[ \t]+[A-Z][A-Za-z'\-]+){0,3})"
                    r",[ \t]*(Chief[ \t]+Justice|Justice|Chief[ \t]+Judge"
                    r"|Presiding[ \t]+Judge|Judge|J\.|C\.J\.)?",
                    chunk,
                )
                if m2:
                    name = m2.group(1).strip()
                    role = self._normalize_role(m2.group(2) or "Judge")
                    if name and len(name) >= 2:
                        entry = f"{name}, {role}"
                        if entry not in result.panel:
                            result.panel.append(entry)
            if result.panel:
                result.confidence["panel"] = 0.75

        # --- Statutes cited ------------------------------------------------
        result.statutes_cited = sorted(set(STATUTE_RE.findall(raw_text)))
        if result.statutes_cited:
            result.confidence["statutes_cited"] = 0.8

        # --- Case-name citations -------------------------------------------
        # Citation extraction is heuristic; v1 stores raw strings. Lower
        # confidence flag so callers can ignore on uncertain regex matches.
        result.citations_to = sorted(set(CITATION_RE.findall(raw_text)))
        if result.citations_to:
            result.confidence["citations_to"] = 0.55

        return result

    # ---------- helpers ----------------------------------------------------

    @staticmethod
    def _normalize_disposition(s: str) -> str:
        cleaned = " ".join(s.split())
        if not cleaned:
            return cleaned
        return cleaned[0].upper() + cleaned[1:].lower()

    @staticmethod
    def _normalize_role(s: str) -> str:
        s = s.strip().rstrip(".").lower()
        mapping = {
            "j.": "Judge",
            "j": "Judge",
            "c.j.": "Chief Judge",
            "cj": "Chief Judge",
            "judge": "Judge",
            "justice": "Justice",
            "chief judge": "Chief Judge",
            "chief justice": "Chief Justice",
        }
        return mapping.get(s, s.title())
