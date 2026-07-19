"""New Hampshire appellate opinion parser.

Handles the New Hampshire Supreme Court -- NH's only appellate court.
The opinion format observed across the 2026 set (modern style, used
since the New Hampshire Reports were renamed to a slip-cite system):

    NOTICE: This opinion is subject to motions for rehearing under
    Rule 22 as well as formal revision before publication in the New
    Hampshire Reports.  Readers are requested to notify the Reporter
    [...]
    https://www.courts.nh.gov/our-courts/supreme-court

    THE SUPREME COURT OF NEW HAMPSHIRE

      ___________________________

    Grafton
    Case No. 2024-0636
    Citation: Martell v. Gold Bess Shooting Club, LLC, 2026 N.H. 1


    CONSTANCE MARTELL & a.

    v.

    GOLD BESS SHOOTING CLUB, LLC & a.

    Argued: November 12, 2025
    Opinion Issued: January 23, 2026

     [counsel section]

     DONOVAN, J.

    [body, paragraphs numbered as [¶1], [¶2], ...]

    Affirmed.

    MACDONALD, C.J., and COUNTWAY and GOULD, JJ., concurred.

Per the resolve_judges generic-byline fallback already covers the
"<NAME>, C.J., and <NAMES>, JJ., concurred." footer, so the parser
doesn't need to populate ``panel`` -- but extracting the byline author
gives `result.author` for the Opinion record.

Fails open: any field the parser can't extract is returned ``None``.
"""
from __future__ import annotations

import re
from datetime import datetime

from .base import ParsedOpinion, StateParser


# ---------- Patterns -----------------------------------------------------

# "THE SUPREME COURT OF NEW HAMPSHIRE" header -- confidence signal that
# this is in fact an NH opinion (vs. a PDF that incidentally cites NH).
NH_HEADER_RE = re.compile(
    r"THE\s+SUPREME\s+COURT\s+OF\s+NEW\s+HAMPSHIRE",
    re.IGNORECASE,
)

# Case number: "Case No. YYYY-NNNN" (4-digit year + 4-digit serial).
# Consolidated appeals use the plural "Case Nos." with multiple numbers
# stacked on subsequent lines -- we capture the FIRST as canonical, since
# the lead docket is what reporters use as the primary citation.
# Historical NH numbering shows up as just "NN-NNN" in some pre-2000
# opinions but the modern post-2000 form is uniform.
CASE_NUMBER_RE = re.compile(
    r"\bCase\s+Nos?\.\s*(\d{4}-\d{3,4})\b"
)

# Citation line carries the canonical case name + year/volume:
#   "Citation: Martell v. Gold Bess Shooting Club, LLC, 2026 N.H. 1"
#   "Citation: State v. Brousseau, 2026 N.H. 10"
#   "Citation: Petition of Metro Treatment of N.H., 2026 N.H. 20"
# Captures (case_name, year, volume).
NH_CITATION_RE = re.compile(
    r"Citation:\s*(?P<case_name>.+?),\s*(?P<year>\d{4})\s+N\.H\.\s*(?P<volume>\d+)",
    re.DOTALL,
)

# Filing dates: NH publishes both an "Argued:" and "Opinion Issued:"
# date. Opinion Issued is the canonical release date.
OPINION_ISSUED_RE = re.compile(
    r"Opinion\s+Issued:\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4})"
)

# Author byline -- three observed forms, all on their own line just
# before the body:
#   "DONOVAN, J."           (associate-justice author)
#   "MACDONALD, C.J."       (chief-justice author)
#   "PER CURIAM"            (no individual author; decided by the whole court)
# All-uppercase surname (NH convention) for the named bylines. The
# footer uses "C.J., and ..., JJ." for concurrence, but that pattern
# always sits after the "concurred" verb and the body so it won't
# trip this top-of-document anchor.
NH_AUTHOR_BYLINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"(?P<author>[A-Z][A-Z\-']+(?:[ \t]+[A-Z][A-Z\-']+)*),[ \t]+(?P<role>C\.J\.|J\.)"
    r"|"
    r"(?P<percuriam>PER\s+CURIAM)"
    r")\.?[ \t]*$",
    re.MULTILINE,
)

# Disposition phrases near the end of the opinion (right before the
# "C.J., and ..., JJ., concurred." footer). Longer matches first since
# the regex returns the leftmost-longest under alternation order.
_DISPOSITION_PATTERNS = (
    r"Affirmed\s+in\s+part;\s+reversed\s+in\s+part(?:;\s+remanded)?",
    r"Affirmed\s+in\s+part,\s+reversed\s+in\s+part(?:,?\s+and\s+remanded)?",
    r"Reversed\s+and\s+remanded",
    r"Vacated\s+and\s+remanded",
    r"Affirmed\s+and\s+remanded",
    r"Reversed\s+in\s+part(?:\s+and\s+remanded)?",
    r"Vacated\s+in\s+part(?:\s+and\s+remanded)?",
    r"Reversed",
    r"Affirmed",
    r"Remanded",
    r"Vacated",
    r"Dismissed",
    # Procedural -- mostly per-curiam orders disposing of motions:
    r"So\s+ordered",
)
DISPOSITION_RE = re.compile(
    r"\b(" + "|".join(_DISPOSITION_PATTERNS) + r")\b",
    re.IGNORECASE,
)

# --- Historic (roughly pre-1980) disposition vocabulary -----------------
# NH opinions from the 19th and early 20th century close with a terse
# procedural disposition instead of the modern "Affirmed." one-liner.
# The corpus break is sharp: modern-style text matches DISPOSITION_RE at
# ~99%, while pre-1980 text matches at near zero.
#
# These stems are frequency-ranked from a scan of 6,000 unmatched NH
# opinions; everything below ~10 occurrences is genuinely one-off prose
# and is deliberately left unmatched rather than guessed at.
#
# NOTE: none of these carry an affirmed / reversed / vacated / remanded /
# dismissed token, so compute_disposition_bucket() files every one of
# them under "other". That is the intended outcome, not an oversight --
# we transcribe the words the court actually wrote and do NOT
# editorialize a 19th-century procedural posture ("exceptions
# overruled") into a modern outcome bucket ("affirmed"). Recording what
# the court said is transcription; deciding what it meant is not ours.
_HISTORIC_CORE = (
    r"(?:(?:the\s+)?(?:plaintiff|defendant|petitioner|respondent)s?[''’]?s?\s+)?"
    r"(?:"
    r"exceptions?\s+(?:overruled|sustained)"
    r"|judgments?\s+on\s+the\s+verdicts?"
    r"|judgments?\s+for\s+(?:the\s+)?(?:plaintiffs?|defendants?)"
    r"|decrees?\s+for\s+(?:the\s+)?(?:plaintiffs?|defendants?)"
    r"|case\s+discharged"
    r"|verdicts?\s+set\s+aside"
    r"|demurrers?\s+(?:overruled|sustained)"
    r"|motions?\s+(?:denied|granted)"
    r"|petitions?\s+(?:denied|granted)"
    r"|appeals?\s+sustained"
    r"|new\s+trial"
    r")"
)

# A historic disposition is matched only as (essentially) the WHOLE final
# sentence -- anchored, not a substring search. "new trial" and "motion
# denied" appear constantly in ordinary body prose ("he moved for a new
# trial"), so an unanchored search would mint false dispositions across
# the corpus. Optional leading filler covers the observed "The order is
# exceptions overruled." framing; the optional trailing clause covers
# compounds like "Exceptions overruled: judgment on the verdict."
HISTORIC_DISPOSITION_RE = re.compile(
    r"^(?:the\s+order\s+is\s+|and\s+)?"
    r"(" + _HISTORIC_CORE + r"(?:\s*[:;,]\s*" + _HISTORIC_CORE + r")?)"
    r"\s*\.?$",
    re.IGNORECASE,
)

# Trailing panel/concurrence footer on historic opinions. The literal
# LAST sentence of these opinions is almost always this footer ("All
# concurred.", "BRANCH, J., did not sit: the others concurred."), so it
# has to come off before the disposition sentence is reachable.
_PANEL_FOOTER_RE = re.compile(
    r"(?:"
    r"(?:[A-Z][A-Za-z\-']+(?:\s+and\s+[A-Z][A-Za-z\-']+)*,?\s*)?"
    r"(?:C\.\s*)?JJ?\.,?\s*(?:did not sit|was absent|took no part[^.]*|dissented|concurred)[^.]*\."
    r"|All\s+concurred\."
    r"|The\s+others\s+concurred\."
    r"|See\s+foot-?note[^.]*\."
    r")\s*$",
    re.IGNORECASE,
)


def _disposition_sentence(raw_text: str) -> str:
    """Return the final sentence of ``raw_text`` with the panel footer removed.

    Historic NH opinions end with a concurrence footer, so the disposition
    is the sentence *before* the literal last one -- and there may be
    several footer sentences stacked ("BRANCH, J., did not sit: the
    others concurred."). Strip them iteratively, then return whatever
    sentence is left at the end.
    """
    text = " ".join(raw_text.split())
    for _ in range(6):
        stripped = _PANEL_FOOTER_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    parts = re.split(r"(?<=[.;])\s+", text[-400:])
    return parts[-1].strip() if parts else text.strip()

# Statute citation -- NH style:  "RSA 159-B:1", "RSA 632-A:2, III (2016)",
# "RSA 126-A:5, VIII".
STATUTE_RE = re.compile(
    r"RSA\s+(\d+(?:-[A-Z])?:\d+(?:,\s*[IVXLCDM]+)?)"
)


# ---------- Parser -------------------------------------------------------

class NewHampshireParser(StateParser):
    state_code = "NH"
    version = "v1"

    def parse(self, raw_text: str) -> ParsedOpinion:
        result = ParsedOpinion()
        if not raw_text:
            return result

        header_conf = 0.9 if NH_HEADER_RE.search(raw_text) else 0.4

        # --- Case number ---------------------------------------------------
        m_num = CASE_NUMBER_RE.search(raw_text)
        if m_num:
            result.case_number = m_num.group(1)
            result.confidence["case_number"] = 0.95

        # --- Case name (from Citation line) --------------------------------
        m_cite = NH_CITATION_RE.search(raw_text)
        if m_cite:
            name = " ".join(m_cite.group("case_name").split())
            if 4 <= len(name) <= 400:
                result.case_name = name
                result.confidence["case_name"] = 0.9
            # Neutral reporter cite, e.g. "2026 N.H. 7" -- the exact form
            # every other NH opinion uses to cite this one (the graph's
            # resolution key) and what a lawyer pastes into search.
            result.reporter_cite = "%s N.H. %s" % (
                m_cite.group("year"), m_cite.group("volume")
            )
            result.confidence["reporter_cite"] = 0.95

        # --- Release date (Opinion Issued) ---------------------------------
        m_filed = OPINION_ISSUED_RE.search(raw_text)
        if m_filed:
            raw_date = m_filed.group(1).replace(",", "")
            for fmt in ("%B %d %Y", "%b %d %Y"):
                try:
                    result.release_date = datetime.strptime(raw_date, fmt).date()
                    result.confidence["release_date"] = 0.95
                    break
                except ValueError:
                    continue

        # --- Precedential --------------------------------------------------
        # NH Supreme published opinions are precedential by default. The
        # NOTICE preamble flags pre-publication revisions but doesn't
        # change precedential status. (Unpublished orders aren't in this
        # corpus -- they don't go through PDF publication.)
        result.is_precedential = True
        result.confidence["is_precedential"] = header_conf

        # --- Disposition ---------------------------------------------------
        # Look in the LAST 1500 chars -- the dispositional one-liner is
        # right before the panel-footer "C.J., and ..., JJ., concurred."
        # signoff. Anchoring at the tail avoids accidentally matching
        # mid-body discussions of "we affirmed Smith" / "we reversed Doe".
        # Three tiers, most trustworthy first:
        #   1. modern one-liner in the tail          (0.85)
        #   2. historic procedural final sentence    (0.80)
        #   3. modern token anywhere in the body     (0.40, last resort)
        # Tier 3 is deliberately last: on a historic opinion it will
        # happily match a passing mention of a case the court "affirmed"
        # below and mint a disposition the court never entered. Tier 2
        # exists mostly to keep tier 3 from having to guess.
        tail = raw_text[-1500:]
        m_modern = DISPOSITION_RE.search(tail)
        m_historic = (
            None if m_modern
            else HISTORIC_DISPOSITION_RE.match(_disposition_sentence(raw_text))
        )
        m_body = (
            None if (m_modern or m_historic)
            else DISPOSITION_RE.search(raw_text)
        )
        if m_modern:
            result.disposition = self._normalize_disposition(m_modern.group(1))
            result.confidence["disposition"] = 0.85
        elif m_historic:
            result.disposition = self._normalize_disposition(m_historic.group(1))
            result.confidence["disposition"] = 0.8
        elif m_body:
            result.disposition = self._normalize_disposition(m_body.group(1))
            result.confidence["disposition"] = 0.4

        # --- Author byline -------------------------------------------------
        # NH bylines sit on their own line just before the body. Three
        # observed forms: associate-justice "LASTNAME, J.", chief-justice
        # "LASTNAME, C.J.", or "PER CURIAM" (no individual author).
        # Search the first 6KB -- the byline always appears within the
        # preamble + counsel block.
        head = raw_text[:6000]
        m_auth = NH_AUTHOR_BYLINE_RE.search(head)
        if m_auth:
            if m_auth.group("percuriam"):
                result.author = "Per Curiam"
                result.confidence["author"] = 0.9
            elif m_auth.group("author"):
                last_name = m_auth.group("author").strip()
                role_raw = (m_auth.group("role") or "").strip()
                # Title-case the all-caps name for display:
                #   "DONOVAN" -> "Donovan", "MACDONALD" -> "Macdonald".
                # Editors can later rename to canonical capitalization
                # (e.g. "MacDonald") via admin.
                display = " ".join(w[:1] + w[1:].lower() for w in last_name.split())
                role = "Chief Justice" if role_raw == "C.J." else "Justice"
                result.author = f"{display}, {role}"
                result.confidence["author"] = 0.9

        # --- Panel (left to resolve_judges generic-byline extractor) ------
        # The NH footer concurrence pattern is already handled by
        # _extract_generic_byline / _PANEL_GROUP_RE in resolve_judges. No
        # need to duplicate that work here -- result.panel stays empty
        # and resolve_judges falls back to its own extractor for NH.

        # --- Statutes cited ------------------------------------------------
        result.statutes_cited = sorted(set(STATUTE_RE.findall(raw_text)))
        if result.statutes_cited:
            result.confidence["statutes_cited"] = 0.8

        return result

    # ---------- helpers ----------------------------------------------------

    @staticmethod
    def _normalize_disposition(s: str) -> str:
        cleaned = " ".join(s.split())
        if not cleaned:
            return cleaned
        return cleaned[0].upper() + cleaned[1:].lower()
