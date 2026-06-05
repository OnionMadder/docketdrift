"""New Hampshire appellate opinion parser.

NH has a single-tier appellate structure: only the Supreme Court of New
Hampshire (no intermediate Court of Appeals). The parser handles NH SCt
opinions only.

Status: **v0 scaffold**. The patterns below encode conventional NH SCt
opinion formatting documented in legal-practice references, but have NOT
yet been verified against a real NH opinion's plain_text body (NH's
judicial website and Justia/FindLaw mirrors block automated fetchers,
and the bulk filter pass is still in progress at the time this scaffold
was written). Once the bulk pass lands and we can inspect ~10 real NH
opinions, this file will need:

- TODO[verify]: case_number regex against actual filed docket strings
- TODO[verify]: filing-date phrase ("Opinion Issued" vs "Argued" -- NH
  distinguishes argument and decision dates; we want the decision)
- TODO[verify]: byline format ("BASSETT, J." UPPERCASE convention vs
  the MN "Larson, Judge" title-case convention)
- TODO[verify]: nonprecedential / memorandum-order marker (NH SCt issues
  some opinions as "Memorandum Order" under Rule 25; need exact phrase)

Reference for the format (conventional, pending verification): a typical
NH Supreme Court opinion opens with::

    THE SUPREME COURT OF NEW HAMPSHIRE
    ___________________________
    [originating court / county]
    No. 2024-0123
    ___________________________

    [PARTY] v. [PARTY]

    Submitted: March 4, 2024
    Opinion Issued: June 18, 2024

    [body...]

    Affirmed.

    BASSETT, J. MacDONALD, C.J., and DONOVAN, J., concurred.

Per-curiam opinions replace the byline with "PER CURIAM".
"""
from __future__ import annotations

import re
from datetime import datetime

from .base import ParsedOpinion, StateParser


# ---------- Patterns -----------------------------------------------------

# Case number: NH SCt uses ``YYYY-NNNN`` (4-digit year + 4-digit sequence),
# sometimes prefixed with "No." in the printed opinion. Sequence numbers
# can be 3 or 4 digits depending on filing year.
# TODO[verify]: confirm against the bulk dump once it lands.
CASE_NUMBER_RE = re.compile(
    r"\b(?:No\.\s*)?(\d{4}-\d{3,4})\b"
)

# Court header. NH SCt opinions identify themselves with "THE SUPREME COURT
# OF NEW HAMPSHIRE" at the top -- we treat finding this as a confidence
# signal that we're really looking at a NH opinion (not, e.g., a federal
# District of New Hampshire opinion accidentally routed here).
COURT_LEVEL_RE = re.compile(
    r"(?:THE\s+)?SUPREME\s+COURT\s+OF\s+NEW\s+HAMPSHIRE",
    re.IGNORECASE,
)

# Filing date. NH publishes both "Argued"/"Submitted" and "Opinion Issued"
# dates; we want the latter (date of decision, the canonical filing date
# for our purposes). Falls back to "Decided" or bare date near the header.
# TODO[verify]: order + naming on real samples.
OPINION_ISSUED_RE = re.compile(
    r"Opinion\s+Issued[:\s]+([A-Za-z\.]+\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)
DECIDED_RE = re.compile(
    r"Decided[:\s]+([A-Za-z\.]+\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)

# Memorandum / nonprecedential marker. NH SCt issues some decisions as
# memorandum orders under Supreme Court Rule 25. The exact phrase varies
# year-to-year; we cover the common forms.
# TODO[verify]: confirm marker phrase on a real memorandum order.
MEMORANDUM_RE = re.compile(
    r"(?:MEMORANDUM\s+ORDER|Memorandum\s+Opinion|Order\s+under\s+Rule\s+25)",
    re.IGNORECASE,
)

# Disposition phrases. Same vocabulary as MN; NH uses Bluebook-standard
# operative verbs. Order matters: longer/more-specific phrases must come
# first because regex returns leftmost match.
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

# NH byline convention: ``BASSETT, J.`` (last name in CAPS, comma, ``J.``).
# Chief Justice uses ``C.J.``. The all-caps surname is the key disambiguator
# from MN's title-case ``Larson, Judge`` -- matching only the all-caps form
# avoids false positives on every random capitalized sentence. The pattern
# anchors at the start of a line but NOT the end, because the lead author
# is often followed on the same line by joiners
# ("BASSETT, J. MacDONALD, C.J., and DONOVAN, J., concurred.").
# TODO[verify]: confirm UPPERCASE convention holds in extracted plain_text
# (PDFs sometimes flatten case during text extraction).
JUDGE_BYLINE_RE = re.compile(
    r"^\s*([A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+){0,2}),\s*(C\.J\.|J\.|JJ\.)",
    re.MULTILINE,
)

# Per-curiam marker (no individual author).
PER_CURIAM_RE = re.compile(r"^\s*PER\s+CURIAM\s*\.?\s*$", re.MULTILINE)

# Panel signature line. After the lead author, joining justices are listed:
# ``MacDONALD, C.J., and DONOVAN, J., concurred.``
PANEL_INTRO_RE = re.compile(
    r"^\s*([A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+){0,2},\s*"
    r"(?:C\.J\.|J\.|JJ\.)[^\.]+?),\s+concurred\.",
    re.MULTILINE,
)

# Statute citation. NH cites its revised statutes as ``RSA 564-B:1-101`` or
# ``RSA 491:18``. The colon between chapter and section is the giveaway.
STATUTE_RE = re.compile(
    r"\bRSA\s+(\d+(?:-[A-Z])?:[A-Za-z0-9\-]+(?:\(\w+\))?)"
)

# Case-name citation. Same heuristic as MN; matches "Party v. Party, Vol Reporter Page (Year)".
CITATION_RE = re.compile(
    r"([A-Z][A-Za-z\.\s']+?\s+v\.\s+[A-Z][A-Za-z\.\s']+?,\s+"
    r"\d+\s+[A-Z][\w\.\s]*?\s+\d+(?:,\s+\d+)?\s+\(\d{4}\))"
)


# ---------- Parser -------------------------------------------------------

class NewHampshireParser(StateParser):
    """v0 NH Supreme Court parser. See module docstring for status."""

    state_code = "NH"
    version = "v0"  # bump to v1 once verified against real opinions

    def parse(self, raw_text: str) -> ParsedOpinion:
        result = ParsedOpinion()
        if not raw_text:
            return result

        # Header confidence -- did we see "SUPREME COURT OF NEW HAMPSHIRE"?
        # If not, every other field gets lower confidence because we might
        # be parsing a federal District of NH opinion or a non-NH document
        # routed to this parser by mistake.
        header_conf = 0.9 if COURT_LEVEL_RE.search(raw_text) else 0.4

        # --- Case number ---------------------------------------------------
        m = CASE_NUMBER_RE.search(raw_text)
        if m:
            result.case_number = m.group(1)
            result.confidence["case_number"] = 0.9 * header_conf

        # --- Release date --------------------------------------------------
        # Prefer "Opinion Issued: [date]" (the canonical filing date).
        # Fall back to "Decided: [date]". Never use "Argued/Submitted"
        # for release_date -- those are oral-argument dates.
        raw_date = None
        for pattern in (OPINION_ISSUED_RE, DECIDED_RE):
            m_filed = pattern.search(raw_text)
            if m_filed:
                raw_date = m_filed.group(1).replace(",", "").replace(".", "")
                break

        if raw_date:
            for fmt in ("%B %d %Y", "%b %d %Y"):
                try:
                    result.release_date = datetime.strptime(raw_date, fmt).date()
                    result.confidence["release_date"] = 0.9 * header_conf
                    break
                except ValueError:
                    continue

        # --- Precedential --------------------------------------------------
        # NH-specific: memorandum orders are nonprecedential. Anything else
        # defaults to precedential (inverse signal compared to MN, where
        # the nonprecedential footer is the explicit marker).
        if MEMORANDUM_RE.search(raw_text):
            result.is_precedential = False
            result.confidence["is_precedential"] = 0.8 * header_conf
        else:
            result.is_precedential = True
            result.confidence["is_precedential"] = header_conf

        # --- Case name -----------------------------------------------------
        # Sits between the case number and the "Submitted:"/"Argued:" line
        # (or "Opinion Issued:" if there's no argument date).
        if result.case_number:
            after_num_idx = raw_text.find(result.case_number)
            if after_num_idx >= 0:
                # Find the earliest date marker that follows the case number.
                date_marker_positions = []
                for pattern in (OPINION_ISSUED_RE, DECIDED_RE,
                                re.compile(r"Submitted[:\s]", re.IGNORECASE),
                                re.compile(r"Argued[:\s]", re.IGNORECASE)):
                    m_marker = pattern.search(raw_text, after_num_idx)
                    if m_marker:
                        date_marker_positions.append(m_marker.start())
                if date_marker_positions:
                    end_idx = min(date_marker_positions)
                    between = raw_text[
                        after_num_idx + len(result.case_number):end_idx
                    ]
                    paragraphs = [
                        p.strip() for p in re.split(r"\n\s*\n", between) if p.strip()
                    ]
                    if paragraphs:
                        candidate = " ".join(paragraphs[0].split())
                        if 4 <= len(candidate) <= 400:
                            result.case_name = candidate
                            result.confidence["case_name"] = 0.65 * header_conf

        # --- Disposition ---------------------------------------------------
        # NH dispositions typically appear at the end of the opinion. The
        # body-search confidence is moderate; if it appears in the LAST
        # ~10% of the text we treat that as stronger signal.
        m_disp = DISPOSITION_RE.search(raw_text)
        if m_disp:
            result.disposition = self._normalize_disposition(m_disp.group(1))
            # Higher confidence if the disposition appears near the end
            # (NH convention is to print it as the last operative word
            # before the byline).
            tail_threshold = int(len(raw_text) * 0.85)
            result.confidence["disposition"] = (
                0.85 if m_disp.start() >= tail_threshold else 0.55
            ) * header_conf

        # --- Author byline / Per curiam ------------------------------------
        if PER_CURIAM_RE.search(raw_text):
            result.author = "Per Curiam"
            result.confidence["author"] = 0.9 * header_conf
        else:
            bylines = JUDGE_BYLINE_RE.findall(raw_text)
            if bylines:
                last_name, role = bylines[0]
                # NH bylines come in UPPERCASE; convert to title-case for
                # consistency with MN's normalized author strings.
                pretty_name = last_name.title().replace("'", "'")
                result.author = f"{pretty_name}, {self._normalize_role(role)}"
                result.confidence["author"] = 0.8 * header_conf

        # --- Panel ---------------------------------------------------------
        # The signature block lists "and DONOVAN, J., concurred" etc.
        # Lead author + joining justices form the panel.
        m_panel = PANEL_INTRO_RE.search(raw_text)
        if m_panel:
            blob = m_panel.group(1)
            chunks = re.split(r",\s+and\s+|,\s+(?=[A-Z]{2,})", blob)
            for chunk in chunks:
                m2 = re.match(
                    r"\s*([A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+){0,2}),?\s*"
                    r"(C\.J\.|J\.|JJ\.)?",
                    chunk,
                )
                if m2:
                    name = m2.group(1).strip().title()
                    role = self._normalize_role(m2.group(2) or "J.")
                    if name and len(name) >= 2:
                        entry = f"{name}, {role}"
                        if entry not in result.panel:
                            result.panel.append(entry)
            if result.panel:
                result.confidence["panel"] = 0.6 * header_conf

        # --- Statutes cited (RSA references) -------------------------------
        result.statutes_cited = sorted(set(
            f"RSA {m}" for m in STATUTE_RE.findall(raw_text)
        ))
        if result.statutes_cited:
            result.confidence["statutes_cited"] = 0.8

        # --- Case-name citations -------------------------------------------
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
            "j.": "Justice",
            "j": "Justice",
            "jj.": "Justice",
            "c.j.": "Chief Justice",
            "cj": "Chief Justice",
            "justice": "Justice",
            "chief justice": "Chief Justice",
        }
        return mapping.get(s, "Justice")
