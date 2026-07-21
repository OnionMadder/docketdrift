"""Arizona appellate opinion parser.

Covers both Arizona appellate courts, which share a house style but differ
in header, docket format, and date format:

  * **Supreme Court of Arizona** (`ariz`). Header ``SUPREME COURT OF THE
    STATE OF ARIZONA``. Docket ``No. CV-25-0009-PR`` (``<TYPE>-<yy>-<seq>-PR``).
    Date ``Filed July 7, 2026``. Author ``JUSTICE CRUZ authored the Opinion of
    the Court``. Court-assigned ``¶N`` paragraph markers.

  * **Court of Appeals** (`arizctapp`), Divisions One and Two. Header
    ``ARIZONA COURT OF APPEALS / DIVISION ONE|TWO``. Docket
    ``No. 1 CA-CV 25-0606 PB`` (Div 1, 2-digit yr) or ``No. 2 CA-CV 2025-0248``
    (Div 2, 4-digit yr). Date ``FILED 07-15-2026``. Author ``Judge Brian Y.
    Furuya delivered the opinion`` / ``Presiding Judge ... delivered`` / ``Vice
    Chief Judge ... authored``.

The field that motivated this parser is **disposition**: without an AZ parser
registered, ``backfill_dispositions --state AZ`` was a silent no-op across
~38K opinions (4.2% populated). Both courts print the disposition as an
ALL-CAPS line in the header block, e.g.::

    No. CV-25-0009-PR
    Filed July 7, 2026
    Appeal from the Superior Court in Navajo County
    The Honorable Melinda K. Hardy, Judge
    No. S0900CV202100003
    AFFIRMED                                     <- disposition of the case
    Memorandum of the Court of Appeals, Division One
    No. 1 CA-CV 23-0723
    VACATED                                      <- what it did to the COA below

On a Supreme petition-for-review the header carries TWO dispositions: what the
Court did to the SUPERIOR COURT (the merits outcome) and, separately, what it
did to the Court of Appeals memorandum. We take the FIRST -- the merits result
is the case outcome; the second only describes the intermediate decision.

Fails open: any field the parser can't extract is returned ``None``/empty.
"""
from __future__ import annotations

import re
from datetime import datetime

from .base import ParsedOpinion, StateParser


# ---------- Court identification ----------------------------------------

AZ_SUPREME_RE = re.compile(
    r"SUPREME\s+COURT\s+OF\s+THE\s+STATE\s+OF\s+ARIZONA", re.IGNORECASE)
AZ_COA_RE = re.compile(
    r"ARIZONA\s+COURT\s+OF\s+APPEALS", re.IGNORECASE)

# Nonprecedential markers -- a COA memorandum decision (or any "not for
# publication" notice) is not precedential; a published OPINION is.
NONPRECEDENTIAL_RE = re.compile(
    r"MEMORANDUM\s+DECISION|NOT\s+FOR\s+PUBLICATION", re.IGNORECASE)


# ---------- Docket numbers ----------------------------------------------

# Supreme: "No. CV-25-0009-PR", "No. CR-24-0140-PR", "No. SA-25-0100-PR".
SUPREME_DOCKET_RE = re.compile(
    r"\bNo\.\s+([A-Z]{2}-\d{2}-\d{3,4}(?:-[A-Z]{1,4})?)\b")

# COA: "No. 1 CA-CV 25-0606 PB" (Div 1, 2-digit yr) or
#      "No. 2 CA-CV 2025-0248" (Div 2, 4-digit yr). Optional case-type suffix
# (PB / FC / SA / PRPC / ...). The internal spaces are part of the canonical
# number the corpus stores, so they're preserved.
COA_DOCKET_RE = re.compile(
    r"\bNo\.\s+(\d\s+CA-[A-Z]{2,4}\s+\d{2,4}-\d{4}(?:\s+[A-Z]{2,4})?)\b")


# ---------- Dates --------------------------------------------------------

# Supreme: "Filed July 7, 2026".
SUPREME_DATE_RE = re.compile(
    r"\bFiled\s+([A-Z][a-z]+\s+\d{1,2},\s*\d{4})")
# COA: "FILED 07-15-2026" (mm-dd-yyyy).
COA_DATE_RE = re.compile(r"\bFILED\s+(\d{2})-(\d{2})-(\d{4})\b")


# ---------- Disposition --------------------------------------------------

# A disposition line is composed ENTIRELY of disposition vocabulary in caps --
# a lone verb ("AFFIRMED") or a compound ("AFFIRMED IN PART, REVERSED IN PART,
# AND REMANDED"). Anchoring to a whole line (vs. a substring search) is what
# keeps an all-caps section heading in the body from being mistaken for the
# disposition.
_DISP_WORD = (r"AFFIRMED|REVERSED|VACATED|REMANDED|DISMISSED|GRANTED|DENIED"
              r"|MODIFIED|QUASHED|APPROVED|REINSTATED")
DISPOSITION_LINE_RE = re.compile(
    r"^[ \t]*("
    r"(?:" + _DISP_WORD + r")"
    r"(?:[ \t,;]+(?:IN|PART|AND|" + _DISP_WORD + r"))*"
    r")\.?[ \t]*$",
    re.MULTILINE,
)

# Tail fallbacks for opinions with no ALL-CAPS header disposition block --
# older opinions, and the modern special-action (CA-SA) / post-conviction
# (PRPC) classes, which dispose in prose at the end rather than in a header.
# All are read only from the tail (operative-conclusion) region.

# Relief-based (special actions / petitions for review): "grants review but
# denies relief", "accept jurisdiction and deny relief", "grant relief". The
# relief outcome is the disposition -- check DENY before GRANT so "grants
# review but denies relief" maps to Denied, not Granted.
RELIEF_DENY_RE = re.compile(r"den(?:y|ies|ied)\s+relief", re.IGNORECASE)
RELIEF_GRANT_RE = re.compile(r"grant(?:s|ed)?\s+relief", re.IGNORECASE)

# Operative verb, active ("we/this court affirm[s]") or passive ("the judgment
# is affirmed"). Group 1 = verb stem.
TAIL_ACTIVE_RE = re.compile(
    r"\b(?:we|this\s+court|the\s+court)\s+"
    r"(?:therefore\s+|accordingly\s+|hereby\s+|thus\s+)?"
    r"(affirm|reverse|vacate|remand|dismiss|quash|modify)(?:s|es)?"
    r"(?P<part>\s+in\s+part)?",
    re.IGNORECASE,
)
TAIL_PASSIVE_RE = re.compile(
    r"\b(?:is|are)\s+(?:hereby\s+)?"
    r"(affirmed|reversed|vacated|remanded|dismissed|quashed|modified)"
    r"(?P<part>\s+in\s+part)?",
    re.IGNORECASE,
)

_VERB_PAST = {
    "affirm": "Affirmed", "reverse": "Reversed", "vacate": "Vacated",
    "remand": "Remanded", "dismiss": "Dismissed", "quash": "Quashed",
    "modify": "Modified",
    "affirmed": "Affirmed", "reversed": "Reversed", "vacated": "Vacated",
    "remanded": "Remanded", "dismissed": "Dismissed", "quashed": "Quashed",
    "modified": "Modified",
}


# "<verb> ... in part" -- reconstruct the actual compound the court wrote.
_IN_PART_RE = re.compile(
    r"(affirm|revers|vacat|remand|dismiss|modif)\w*\s+in\s+part", re.IGNORECASE)
_STEM_WORD = {"affirm": "Affirmed", "revers": "Reversed", "vacat": "Vacated",
              "remand": "Remanded", "dismiss": "Dismissed", "modif": "Modified"}
# A split disposition with no literal "in part": "otherwise affirm", "affirm in
# all other respects".
_OTHERWISE_RE = re.compile(
    r"otherwise\s+(?:affirm|revers|vacat)|in\s+all\s+other\s+respects?",
    re.IGNORECASE)


def _tail_disposition(tail: str) -> str | None:
    """Operative disposition from the tail region, or None.

    Precedence matters, because a subsidiary action in an earlier sentence
    ("we dismiss the cross-appeal ... and otherwise affirm") must not be read
    as the primary disposition:

      1. Relief outcome (special actions / petitions) -- deny before grant.
      2. Explicit "<verb> in part" compounds -> reconstruct them verbatim
         (buckets to mixed).
      3. Split signalled without "in part" ("otherwise affirm" / "in all other
         respects"), or affirm co-occurring with reverse/vacate -> mixed.
      4. A single operative verb -- take the LAST occurrence, i.e. the final
         disposition sentence, not an earlier subsidiary one.
    """
    if RELIEF_DENY_RE.search(tail):
        return "Denied"
    if RELIEF_GRANT_RE.search(tail):
        return "Granted"

    parts = _IN_PART_RE.findall(tail)
    if parts:
        seen, phrase = set(), []
        for stem in parts:
            w = _STEM_WORD[stem.lower()]
            if w not in seen:
                seen.add(w)
                phrase.append("%s in part" % w)
        joined = ", ".join(phrase)
        return joined[:1].upper() + joined[1:].lower()  # sentence-case

    low = tail.lower()
    has_affirm = re.search(r"\baffirm", low)
    has_undo = re.search(r"\b(revers|vacat)", low)
    if _OTHERWISE_RE.search(tail) or (has_affirm and has_undo):
        # A split we can't itemize precisely; "Affirmed in part" is directional
        # and honest, and buckets to mixed.
        return "Affirmed in part"

    matches = list(TAIL_ACTIVE_RE.finditer(tail)) or list(TAIL_PASSIVE_RE.finditer(tail))
    if matches:
        m = matches[-1]
        disp = _VERB_PAST[m.group(1).lower()]
        if m.group("part"):
            disp += " in part"
        return disp
    return None


# ---------- Author byline ------------------------------------------------

# Supreme: "JUSTICE CRUZ authored the Opinion of the Court" (also CHIEF /
# VICE CHIEF JUSTICE). The surname is all-caps here.
SUPREME_AUTHOR_RE = re.compile(
    r"\b(?:(?P<chief>CHIEF|VICE\s+CHIEF)\s+)?JUSTICE\s+"
    r"(?P<name>[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,2})\s+"
    r"authored\s+the\s+Opinion")

# COA: "Judge Brian Y. Furuya delivered the opinion" / "Presiding Judge ...
# delivered" / "Vice Chief Judge ... authored the opinion". Mixed-case name.
COA_AUTHOR_RE = re.compile(
    r"\b(?P<role>(?:Presiding\s+|Chief\s+|Vice\s+Chief\s+)?Judge)\s+"
    r"(?P<name>[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,2})\s+"
    r"(?:delivered|authored)\s+the\s+(?:[Oo]pinion|[Dd]ecision)")


# ---------- Case name ----------------------------------------------------

# Running page header, e.g. "MAYWALD V. TOYOTA" -- the most consistent,
# already-abbreviated case-name form in the text. In-re / estate matters have
# no "V." header and are left to the CL-supplied case_name.
CASE_NAME_HEADER_RE = re.compile(
    r"^[ \t]*([A-Z][A-Z0-9 .,'&\-]{2,60}?)\s+V\.\s+([A-Z][A-Z0-9 .,'&\-]{2,60}?)[ \t]*$",
    re.MULTILINE)


# ---------- Statutes -----------------------------------------------------

# A.R.S. citations: "A.R.S. § 12-820.05", "A.R.S. §§ 13-1105 to -1108".
STATUTE_RE = re.compile(
    r"A\.R\.S\.\s*§{1,2}\s*(\d+(?:\.\d+)?-\d+(?:\.\d+)?)")


def _titlecase_caps(s: str) -> str:
    """Title-case an ALL-CAPS token/name for display ("CRUZ" -> "Cruz")."""
    return " ".join(w[:1] + w[1:].lower() if w.isupper() else w
                    for w in s.split())


class ArizonaParser(StateParser):
    state_code = "AZ"
    version = "v1"

    def parse(self, raw_text: str) -> ParsedOpinion:
        result = ParsedOpinion()
        if not raw_text:
            return result

        head = raw_text[:6000]
        is_supreme = bool(AZ_SUPREME_RE.search(head))
        is_coa = bool(AZ_COA_RE.search(head))
        header_conf = 0.9 if (is_supreme or is_coa) else 0.3

        # --- Docket number ---------------------------------------------
        if is_supreme:
            m = SUPREME_DOCKET_RE.search(head)
        elif is_coa:
            m = COA_DOCKET_RE.search(head)
        else:
            m = SUPREME_DOCKET_RE.search(head) or COA_DOCKET_RE.search(head)
        if m:
            result.case_number = " ".join(m.group(1).split())
            result.confidence["case_number"] = header_conf

        # --- Release date ----------------------------------------------
        md = SUPREME_DATE_RE.search(head)
        if md:
            for fmt in ("%B %d %Y", "%b %d %Y"):
                try:
                    result.release_date = datetime.strptime(
                        md.group(1).replace(",", ""), fmt).date()
                    result.confidence["release_date"] = 0.95
                    break
                except ValueError:
                    continue
        if result.release_date is None:
            mc = COA_DATE_RE.search(head)
            if mc:
                mm, dd, yyyy = mc.groups()
                try:
                    result.release_date = datetime(
                        int(yyyy), int(mm), int(dd)).date()
                    result.confidence["release_date"] = 0.95
                except ValueError:
                    pass

        # --- Precedential ----------------------------------------------
        # Supreme opinions are always precedential; a COA memorandum decision
        # (or explicit "not for publication") is not.
        if is_supreme:
            result.is_precedential = True
            result.confidence["is_precedential"] = header_conf
        elif is_coa:
            result.is_precedential = not bool(NONPRECEDENTIAL_RE.search(head))
            result.confidence["is_precedential"] = 0.85

        # --- Disposition -----------------------------------------------
        # Search only the header block (before COUNSEL) for the ALL-CAPS
        # disposition line, and take the FIRST -- on a Supreme PR case that's
        # the merits (superior-court) outcome, not the later COA disposition.
        cut = head.upper().find("COUNSEL")
        header_region = head[:cut] if cut != -1 else head
        dm = DISPOSITION_LINE_RE.search(header_region)
        if dm:
            # Sentence-case the phrase, not title-case: "AFFIRMED IN PART,
            # REVERSED IN PART" -> "Affirmed in part, reversed in part" (which
            # buckets to "mixed"), never "Affirmed In Part".
            phrase = " ".join(dm.group(1).split())
            result.disposition = phrase[:1].upper() + phrase[1:].lower()
            result.confidence["disposition"] = 0.9
        else:
            # No header block (older opinions, special actions, PRPC petitions):
            # read the operative disposition from the tail region only.
            tail_disp = _tail_disposition(raw_text[-1800:])
            if tail_disp:
                result.disposition = tail_disp
                result.confidence["disposition"] = 0.5

        # --- Author ----------------------------------------------------
        if is_supreme:
            am = SUPREME_AUTHOR_RE.search(head)
            if am:
                role = "Chief Justice" if am.group("chief") else "Justice"
                result.author = "%s, %s" % (
                    _titlecase_caps(am.group("name")), role)
                result.confidence["author"] = 0.9
        if result.author is None:
            am = COA_AUTHOR_RE.search(raw_text[:8000])
            if am:
                role = " ".join(am.group("role").split())  # "Presiding Judge"
                result.author = "%s, %s" % (am.group("name").strip(), role)
                result.confidence["author"] = 0.9

        # --- Case name -------------------------------------------------
        nm = CASE_NAME_HEADER_RE.search(raw_text[:5000])
        if nm:
            left = _titlecase_caps(nm.group(1).strip(" .,"))
            right = _titlecase_caps(nm.group(2).strip(" .,"))
            if left and right:
                result.case_name = "%s v. %s" % (left, right)
                result.confidence["case_name"] = 0.6

        # --- Statutes cited --------------------------------------------
        result.statutes_cited = sorted(set(STATUTE_RE.findall(raw_text)))
        if result.statutes_cited:
            result.confidence["statutes_cited"] = 0.8

        return result
