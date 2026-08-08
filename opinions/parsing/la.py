"""Louisiana appellate opinion parser.

Covers both Louisiana appellate courts. Each has its own header, docket
format, and byline convention:

  * **Supreme Court of Louisiana** (`la`). Cover page is a news release
    from the Clerk of Court, then the signed opinion begins with the
    header ``SUPREME COURT OF LOUISIANA``. Docket ``2026-CD-00927``
    (``YYYY-<TYPE>-NNNNN``, hyphens). Date ``handed down on the 31st
    day of July, 2026`` on the cover page. Byline is either
    ``PER CURIAM:`` or ``<SURNAME>, J.:``, with dissent/concur lines
    below (``Weimer, C.J., dissents and assigns reasons.``) that name
    the participating panelists explicitly -- the implicit signer plus
    every explicit line adds up to the 7-seat panel.

  * **Court of Appeal** (`lactapp` in CL; five circuit divisions).
    Header ``COURT OF APPEAL`` + a separate ``<ORDINAL> CIRCUIT`` line
    (extraction-scrambled from the tabular caption). The circuit is
    exposed as ``confidence['circuit']`` (1.0-5.0) for the later
    ``assign_la_circuits`` command to consume -- the parser itself does
    NOT decide which of our 5 Court rows the opinion belongs on.

    Docket format varies BY CIRCUIT. From measured samples:
      - 1st Cir: ``2026 CW 0867`` (space-separated; matches CL bulk normalization)
      - 4th Cir: ``2023-K-0791`` / ``2025-CA-0604`` (long-year hyphen)
      - 5th Cir: ``24-C-619`` (short-year hyphen)
    All three land into ``case_number`` verbatim; ``normalize_case_numbers``
    (if extended for LA) can canonicalize later. The docket format itself
    is a strong secondary circuit signal.

    Byline (in decreasing signal strength):
      1. ``Judge <Name>`` between paired ``******`` markers (4th Cir)
      2. ``Panel composed of Judges X, Y, and Z`` (5th Cir; author = first)
      3. ``BEFORE : X, Y, AND Z, JJ.`` (1st Cir; PER CURIAM if no attribution)

    Disposition sits either:
      - As a right-aligned all-caps line above the body (``VACATED AND REMANDED``)
      - As the first body line (``WRIT GRANTED IN PART AND DENIED IN PART``)
      - As a header sentence (``WRIT NOT CONSIDERED.``)

Louisiana civil-law dispositions include writ actions that don't map into
the common-law affirmed/reversed vocabulary (WRIT GRANTED / WRIT DENIED /
WRIT NOT CONSIDERED). Transcribe those verbatim -- ``compute_disposition_bucket``
will file them as ``other`` unless a mapping is added later. Do NOT
force-map here (the NH historic-tier lesson).

Fails open: any field the parser can't extract is returned ``None``/empty.
"""
from __future__ import annotations

import re
from datetime import datetime

from .base import ParsedOpinion, StateParser


# ---------- Court identification ----------------------------------------

LA_SUPREME_RE = re.compile(
    r"SUPREME\s+COURT\s+OF\s+LOUISIANA", re.IGNORECASE)
LA_COA_RE = re.compile(
    r"COURT\s+OF\s+APPEAL(?:[,\s]|$)", re.IGNORECASE)

LA_CIRCUIT_RE = re.compile(
    r"\b(FIRST|SECOND|THIRD|FOURTH|FIFTH)\s+CIRCUIT\b", re.IGNORECASE)
_CIRCUIT_TO_DIV = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}

# COA nonprecedential marker.
NONPRECEDENTIAL_RE = re.compile(
    r"NOT\s+DESIGNATED\s+FOR\s+PUBLICATION", re.IGNORECASE)


# ---------- Docket numbers ----------------------------------------------

# Supreme: "No. 2026-CD-00927" (also appears as bare "2026-CD-00927" in the
# news-release block on page 1 before the "No." prefix on page 2).
SUPREME_DOCKET_RE = re.compile(
    r"\b(?:No\.\s+)?(\d{4}-[A-Z]{1,3}-\d{4,5})\b")

# COA -- three per-circuit formats. Try in order of specificity; the first
# match wins. All use a leading "NO." (occasionally lowercase after OCR).
COA_DOCKET_LONG_RE = re.compile(   # 4th Cir long-year hyphen
    r"\bNO\.\s+(\d{4}-[A-Z]{1,3}-\d{3,5})\b", re.IGNORECASE)
COA_DOCKET_SPACE_RE = re.compile(  # 1st Cir space-separated
    r"\bNO\.\s+(\d{4}\s+[A-Z]{1,3}\s+\d{3,5})\b", re.IGNORECASE)
COA_DOCKET_SHORT_RE = re.compile(  # 5th Cir short-year hyphen
    r"\bNO\.\s+(\d{2}-[A-Z]{1,3}-\d{3,5})\b", re.IGNORECASE)


# ---------- Dates --------------------------------------------------------

# Supreme cover page: "The Opinions handed down on the 31st day of July, 2026".
SUPREME_DATE_RE = re.compile(
    r"handed\s+down\s+on\s+the\s+(\d+)(?:st|nd|rd|th)?\s+day\s+of\s+"
    r"([A-Za-z]+),?\s+(\d{4})",
    re.IGNORECASE)

# COA: "December 30, 2024" (mixed case) or "DECEMBER 30, 2025" (all caps).
# One combined regex, then case-normalize before parsing.
COA_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE)


# ---------- Disposition --------------------------------------------------

# Vocabulary spans common-law and civil-law dispositions. WRIT-family
# entries are what makes this different from AZ/MN/NH. STAY and INJUNCTION
# are the noun objects of a disposition verb ("STAY LIFTED"), so they're
# whitelisted as part of the phrase, not as verbs on their own -- the
# LIFTED/DISSOLVED verb is what qualifies the line.
_DISP_VERB = (
    r"AFFIRMED|REVERSED|VACATED|REMANDED|DISMISSED|GRANTED|DENIED"
    r"|MODIFIED|QUASHED|WITHDRAWN|RENDERED|LIFTED|DISSOLVED|RECALLED"
    r"|CONSIDERED|MOOT|REINSTATED|ISSUED"
)
# PER and CURIAM deliberately NOT here -- they only appear in disposition
# context inside "SEE PER CURIAM" (a pointer, not a disposition), and the
# cover_cut logic handles that separately. Leaving them in the vocabulary
# would cause the byline heading "PER CURIAM:" to match as a disposition
# on truncated inputs.
_DISP_NOUN_OR_MOD = r"WRIT|STAY|INJUNCTION|REHEARING|SEE|NOT"
_DISP_CONNECTIVE = r"IN|PART|AND|OF|AS|TO|MODIFIED"

# A disposition LINE is composed entirely of tokens from the vocabulary
# above. Two anchor shapes:
#   (a) whole-line all-caps phrase, optional trailing period ("VACATED
#       AND REMANDED", "WRIT GRANTED IN PART AND DENIED IN PART")
#   (b) line STARTS with the phrase + period + rest of body prose
#       ("WRIT NOT CONSIDERED. This writ application failed to include...")
# Rejects section headings that happen to be in caps but include words
# outside the vocabulary.
_DISP_PHRASE = (
    r"(?:" + _DISP_VERB + r"|" + _DISP_NOUN_OR_MOD + r")"
    r"(?:[ \t,;.]+(?:" + _DISP_VERB + r"|" + _DISP_NOUN_OR_MOD
    + r"|" + _DISP_CONNECTIVE + r"))*"
)
DISPOSITION_LINE_RE = re.compile(
    r"^[ \t]*(" + _DISP_PHRASE + r")\.?[ \t]*$",
    re.MULTILINE,
)
# Fallback: disposition at line-start followed by a period and body prose.
# Requires TWO+ tokens (`WRIT` alone or `NOT` alone isn't a disposition;
# `WRIT NOT CONSIDERED` is). The 2+ requirement is what keeps ordinary
# body sentences beginning with "The court..." out of the match.
DISPOSITION_INLINE_RE = re.compile(
    r"^[ \t]*("
    r"(?:" + _DISP_VERB + r"|" + _DISP_NOUN_OR_MOD + r")"
    r"(?:[ \t,;]+(?:" + _DISP_VERB + r"|" + _DISP_NOUN_OR_MOD
    + r"|" + _DISP_CONNECTIVE + r")){1,}"
    r")\.\s+[A-Z]",
    re.MULTILINE,
)

# For Supreme opinions, the disposition sits inside a news-release block on
# page 1 that has multiple all-caps sentences separated by periods and can
# span pypdf-scrambled line breaks (e.g. "STAY\n LIFTED. INJUNCTION
# LIFTED. REVERSED AND RENDERED."). Grab the RUN of disposition sentences,
# not just one line -- then normalize the whitespace.
SUPREME_DISP_BLOCK_RE = re.compile(
    r"(?:(?:" + _DISP_VERB + r"|" + _DISP_NOUN_OR_MOD + r"|"
    + _DISP_CONNECTIVE + r")\b[\s.,]*){2,}",
    re.MULTILINE,
)


# ---------- Author byline ------------------------------------------------

# Supreme: "PER CURIAM:" heading OR "<SURNAME>, J.:" / "<SURNAME>, C.J.:"
# heading on the opinion body (page 2+). pypdf often splits capitalized
# tokens across a line break ("PE\n R CURIAM:", "B\nurris"), so allow any
# amount of whitespace INSIDE the word.
SUPREME_PER_CURIAM_RE = re.compile(
    r"P\s*E\s*R\s+C\s*U\s*R\s*I\s*A\s*M\s*:",
    re.IGNORECASE)
SUPREME_SIGNED_RE = re.compile(
    r"^[ \t]*([A-Z][A-Z'\-]{1,20}),\s*(C\.?J\.?|J\.?)\s*:",
    re.MULTILINE)

# Supreme concur/dissent LINES (used to enumerate the 7-seat panel):
#   "Weimer, C.J., dissents and assigns reasons."
#   "McCallum, J., additionally concurs and assigns reasons."
# pypdf can split the surname across a linebreak ("B\nurris") so the
# name-capture is line-insensitive: capture any run of letters/apostrophes
# /hyphens/newlines (max ~30 chars) that starts with a cap and precedes a
# `, J.` role token.
SUPREME_PANEL_LINE_RE = re.compile(
    r"^[ \t]*([A-Z][A-Za-z'\-\s]{1,25}?),\s*(C\.?J\.?|J\.?),\s*"
    r"(?:additionally\s+)?(concur|dissent)",
    re.MULTILINE)

# COA: "Judge Joy Cossich Lobrano" between paired ****** markers.
COA_STARRED_AUTHOR_RE = re.compile(
    r"\*{5,}\s*\n\s*(?:Chief\s+Judge\s+|Judge\s+)"
    r"([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){1,3})"
    r"\s*\n\s*\*{5,}",
    re.MULTILINE)

# COA 5th Circuit panel line: "Panel composed of Judges Marc E. Johnson,
# John J. Molaison, Jr., and Scott U. Schlegel". Author is the first named.
COA_PANEL_COMPOSED_RE = re.compile(
    r"Panel\s+composed\s+of\s+Judges?\s+"
    r"(?P<first>[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){1,3})",
    re.IGNORECASE)

# COA 4th Circuit "(Court composed of Judge X, Judge Y, Judge Z)" -- gives
# us the panel roster (author separately identified by STARRED_AUTHOR).
COA_COURT_COMPOSED_RE = re.compile(
    r"\(Court\s+composed\s+of\s+((?:Judge\s+[A-Z][A-Za-z.'\-]+"
    r"(?:\s+[A-Z][A-Za-z.'\-]+){0,3},?\s*)+)\)",
    re.IGNORECASE)

# COA 1st Circuit "BEFORE : WOLFE, STROMBERG, AND BALFOUR, JJ."
COA_BEFORE_RE = re.compile(
    r"BEFORE\s*:\s*([A-Z][A-Z, .'\-]+?),?\s+J{1,2}\.",
    re.MULTILINE)


def _titlecase_caps(s: str) -> str:
    """Title-case an ALL-CAPS token/name for display ('WEIMER' -> 'Weimer')."""
    return " ".join(w[:1] + w[1:].lower() if w.isupper() else w
                    for w in s.split())


def _month_num(month_name: str) -> int | None:
    """Case-insensitive month name -> 1..12, or None on unknown."""
    try:
        return datetime.strptime(month_name.strip()[:3].title(), "%b").month
    except ValueError:
        return None


class LouisianaParser(StateParser):
    state_code = "LA"
    version = "v1"

    def parse(self, raw_text: str) -> ParsedOpinion:
        result = ParsedOpinion()
        if not raw_text:
            return result

        # Generous head region: Supreme opinions have a cover page + panel
        # signals well past the first 3-4K; COA opinions carry byline and
        # panel through the counsel block (~5K in).
        head = raw_text[:8000]

        is_supreme = bool(LA_SUPREME_RE.search(head))
        is_coa = bool(LA_COA_RE.search(head)) and not is_supreme
        header_conf = 0.9 if (is_supreme or is_coa) else 0.3

        # --- Circuit (COA only) ----------------------------------------
        # Exposed as confidence['circuit'] for assign_la_circuits (Phase 7c)
        # to consume; the parser itself does NOT change the Opinion.court FK.
        if is_coa:
            cm = LA_CIRCUIT_RE.search(head)
            if cm:
                result.confidence["circuit"] = float(
                    _CIRCUIT_TO_DIV[cm.group(1).lower()])

        # --- Docket number ---------------------------------------------
        if is_supreme:
            m = SUPREME_DOCKET_RE.search(head)
            if m:
                result.case_number = m.group(1)
                result.confidence["case_number"] = header_conf
        elif is_coa:
            for pat in (COA_DOCKET_LONG_RE, COA_DOCKET_SPACE_RE,
                        COA_DOCKET_SHORT_RE):
                m = pat.search(head)
                if m:
                    # Normalize whitespace inside the docket (multiple
                    # spaces from tabular columns -> single space).
                    result.case_number = " ".join(m.group(1).split())
                    result.confidence["case_number"] = header_conf
                    break

        # --- Release date ----------------------------------------------
        if is_supreme:
            m = SUPREME_DATE_RE.search(head)
            if m:
                day, month, year = m.groups()
                mo = _month_num(month)
                if mo:
                    try:
                        result.release_date = datetime(
                            int(year), mo, int(day)).date()
                        result.confidence["release_date"] = 0.95
                    except ValueError:
                        pass
        if result.release_date is None:
            # COA and Supreme fallback: "December 30, 2024" / "DECEMBER
            # 30, 2025". Take the FIRST valid date in the head region --
            # the release date is one of the earliest datestamps on a
            # COA opinion (right-aligned above the body).
            for match in COA_DATE_RE.finditer(head):
                mo = _month_num(match.group(1))
                if not mo:
                    continue
                try:
                    result.release_date = datetime(
                        int(match.group(3)), mo, int(match.group(2))).date()
                    result.confidence["release_date"] = 0.8
                    break
                except ValueError:
                    continue

        # --- Precedential ----------------------------------------------
        # Supreme opinions are always precedential. For COA, explicit
        # "NOT DESIGNATED FOR PUBLICATION" is the negative marker;
        # otherwise defer to CL bulk's precedential_status via the
        # ingest layer (we do NOT decide from silence here).
        if is_supreme:
            result.is_precedential = True
            result.confidence["is_precedential"] = header_conf
        elif is_coa:
            if NONPRECEDENTIAL_RE.search(head):
                result.is_precedential = False
                result.confidence["is_precedential"] = 0.9
            # Silence -> leave None; CL bulk provides the field.

        # --- Disposition -----------------------------------------------
        if is_supreme:
            # The disposition sits in the news-release block on the cover
            # page, before "SEE PER CURIAM" if present. Grab a run of
            # disposition sentences and normalize.
            cover = raw_text[:2000]
            # Cut off after PER CURIAM marker if it appears (Supreme
            # cover pages end with "SEE PER CURIAM." then the concur/
            # dissent lines, which are not the disposition).
            cover_cut = cover.upper().find("SEE PER CURIAM")
            if cover_cut > 0:
                cover = cover[:cover_cut]
            # Look for the run of disposition-vocabulary sentences.
            bm = SUPREME_DISP_BLOCK_RE.search(cover)
            if bm:
                phrase = " ".join(bm.group(0).split())
                # Trim trailing punctuation, keep periods between clauses.
                phrase = phrase.rstrip(" ,.;")
                # Drop pointer sentences ("See per curiam", "See below")
                # -- they don't state the disposition, they redirect
                # to the opinion text below.
                sentences = [s.strip() for s in phrase.split(".") if s.strip()]
                sentences = [s for s in sentences
                             if not re.match(r"^\s*SEE\b", s, re.IGNORECASE)]
                sentences = [s[:1].upper() + s[1:].lower() for s in sentences]
                if sentences:
                    result.disposition = ". ".join(sentences) + "."
                    result.confidence["disposition"] = 0.85
        elif is_coa:
            # COA dispositions sit as an all-caps phrase near the top of
            # the body. Try the whole-line shape first (right-aligned
            # "VACATED AND REMANDED" or standalone "WRIT GRANTED IN PART
            # AND DENIED IN PART"), then the inline shape ("WRIT NOT
            # CONSIDERED. This writ application failed..."). Both search
            # only the first ~4000 chars, preventing an in-body all-caps
            # section heading from being read as a disposition.
            head_zone = raw_text[:4000]
            dm = (DISPOSITION_LINE_RE.search(head_zone) or
                  DISPOSITION_INLINE_RE.search(head_zone))
            if dm:
                phrase = " ".join(dm.group(1).split())
                result.disposition = (
                    phrase[:1].upper() + phrase[1:].lower())
                result.confidence["disposition"] = 0.85

        # --- Author byline ---------------------------------------------
        if is_supreme:
            # Signed opinion beats per curiam if both appear (rare, but a
            # concurring justice's separate opinion also begins with a
            # signed heading). Check signed FIRST, then fall back to
            # per curiam.
            sm = SUPREME_SIGNED_RE.search(raw_text[:4000])
            if sm:
                surname = _titlecase_caps(sm.group(1))
                role = ("Chief Justice" if "C" in sm.group(2).upper()
                        else "Justice")
                result.author = f"{surname}, {role}"
                result.confidence["author"] = 0.9
            elif SUPREME_PER_CURIAM_RE.search(raw_text[:4000]):
                result.author = "Per Curiam"
                result.confidence["author"] = 0.9
        elif is_coa:
            # Prefer starred author (4th Cir) -- most explicit. Then
            # panel-composed (5th Cir). Then BEFORE line (1st Cir, panel
            # only -- no author extractable, opinion may be per curiam).
            am = COA_STARRED_AUTHOR_RE.search(raw_text[:6000])
            if am:
                result.author = f"Judge {am.group(1).strip()}"
                result.confidence["author"] = 0.9
            else:
                pm = COA_PANEL_COMPOSED_RE.search(raw_text[:6000])
                if pm:
                    result.author = f"Judge {pm.group('first').strip()}"
                    result.confidence["author"] = 0.7

        # --- Panel -----------------------------------------------------
        # Union of every extraction path -- callers can dedupe later.
        panel: list[str] = []
        if is_supreme:
            # Per-curiam Supreme opinions list every non-authoring
            # justice's concur/dissent line explicitly. Add each named
            # justice; the implicit authoring justice(s) are not named
            # in the same block and will be added by the byline pass.
            for pm in SUPREME_PANEL_LINE_RE.finditer(raw_text[:5000]):
                # Normalize any whitespace inside the surname (pypdf
                # splits "Burris" as "B\nurris").
                surname = _titlecase_caps("".join(pm.group(1).split()))
                if surname not in panel:
                    panel.append(surname)
        elif is_coa:
            # 4th Cir "(Court composed of Judge X, Judge Y, Judge Z)".
            # The block can span line breaks (pypdf column artifacts), so
            # normalize internal whitespace on each captured name.
            cm = COA_COURT_COMPOSED_RE.search(raw_text[:6000])
            if cm:
                for part in cm.group(1).split(","):
                    name = re.sub(r"^\s*(?:Chief\s+)?Judge\s+", "",
                                  part.strip(), flags=re.IGNORECASE)
                    name = " ".join(name.split())  # collapse \n and runs
                    if name and name not in panel:
                        panel.append(name)
            # 5th Cir "Panel composed of Judges A, B, and C" -- capture
            # the FULL comma-and-"and"-separated tail after the marker.
            if not panel:
                pm = COA_PANEL_COMPOSED_RE.search(raw_text[:6000])
                if pm:
                    # Take everything after "Judges " up to the next
                    # paren, newline pair, or end of the surrounding
                    # sentence. Keep single line-wraps -- names span
                    # them in tabular text.
                    tail = raw_text[pm.start():pm.start() + 400]
                    names_blob = re.split(
                        r"Panel\s+composed\s+of\s+Judges?\s+",
                        tail, maxsplit=1, flags=re.IGNORECASE)[-1]
                    # Cut at double newline or paren
                    names_blob = re.split(r"\n\s*\n|[()]",
                                          names_blob, 1)[0]
                    # Split on ", and" / "," / " and " (word-boundary
                    # "and" so it doesn't split "Alexander")
                    for chunk in re.split(
                            r",\s+and\s+|,\s*|\s+and\s+", names_blob):
                        chunk = " ".join(chunk.split()).rstrip(".")
                        if chunk and re.match(
                                r"^[A-Z][A-Za-z.'\-]+"
                                r"(?:\s+[A-Z][A-Za-z.'\-]+)+"
                                r"(?:,?\s+(?:Jr|Sr|II|III|IV)\.?)?$",
                                chunk):
                            if chunk not in panel:
                                panel.append(chunk)
            # 1st Cir "BEFORE : WOLFE, STROMBERG, AND BALFOUR, JJ."
            if not panel:
                bm = COA_BEFORE_RE.search(raw_text[:6000])
                if bm:
                    for chunk in re.split(r",\s*(?:AND\s+)?|\s+AND\s+",
                                          bm.group(1)):
                        chunk = chunk.strip().strip(".").strip()
                        if chunk and chunk.replace("-", "").replace("'", "").isalpha():
                            surname = _titlecase_caps(chunk)
                            if surname not in panel:
                                panel.append(surname)
        result.panel = panel
        if panel:
            result.confidence["panel"] = 0.8

        return result
