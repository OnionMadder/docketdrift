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
FILED_DATE_RE = re.compile(r"Filed\s+([A-Za-z\.]+\s+\d{1,2},?\s+\d{4})")

# Order opinions ("ORDER OPINION") don't carry a "Filed [date]" line -- they
# date themselves at the foot: ``Dated: January 6, 2026 BY THE COURT``. Used as
# a fallback when FILED_DATE_RE misses so this whole class (HRO appeals, other
# procedural dispositions -- absent from CourtListener) can be ingested.
DATED_DATE_RE = re.compile(r"Dated:\s*([A-Za-z\.]+\s+\d{1,2},?\s+\d{4})")

# Header that closes the caption block in an order opinion (stands in for the
# "Filed [date]" boundary regular opinions use).
ORDER_OPINION_HDR_RE = re.compile(r"\bORDER\s+OPINION\b")

# Footer marker that flips is_precedential to False. Regular opinions say
# "This opinion ... is nonprecedential"; order opinions say "this order opinion
# is nonprecedential" (per Minn. R. Civ. App. P. 136.01, subd. 1(c)).
NONPRECEDENTIAL_RE = re.compile(
    r"This\s+(?:order\s+)?opinion\s+(?:will\s+be\s+unpublished\s+and\s+)?is\s+nonprecedential",
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
JUDGE_BYLINE_RE = re.compile(
    r"^[ \t]*([A-Z][A-Za-z'\-]+(?:[ \t]+[A-Z][A-Za-z'\-]+){0,3}),[ \t]*"
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
        if result.case_number and boundary is not None:
            after_num_idx = raw_text.find(result.case_number)
            if after_num_idx >= 0:
                between = raw_text[after_num_idx + len(result.case_number):boundary]
                paragraphs = [p.strip() for p in re.split(r"\n\s*\n", between) if p.strip()]
                if m_filed:
                    # Regular: the caption is the first paragraph block (it
                    # wraps across lines but stops at a blank line).
                    candidate = " ".join(paragraphs[0].split()) if paragraphs else ""
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
