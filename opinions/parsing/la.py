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

# ---- Tail fallback tier (reporter-style CL bulk texts) ------------------
#
# The head-zone patterns above were built against the modern lasc.gov /
# COA PDF layouts. The CL bulk corpus is West-reporter OCR with different
# conventions, measured 2026-08-18 on a 1,200-row sample of the
# disposition='' corpus:
#   - ALLCAPS disposition at the TAIL ("AFFIRMED IN PART; REVERSED IN
#     PART; AND REMANDED WITH INSTRUCTIONS." often followed by page-number
#     junk like "31 32")  -- ~10%+
#   - LA Supreme writ-table entries ending in a bare sentence "Denied." /
#     "Granted." after a docket recitation ("...applying for writ of
#     certiorari...; No. 654-79. Denied."), sometimes followed by a short
#     citation reason  -- the dominant unmatched class
#   - WRIT GRANTED/DENIED phrases in the tail  -- ~6%
# Both patterns are ANCHORED (whole-sentence / end-of-document), never
# substring prose matches -- the NH 2026-07-19 lesson: an unanchored body
# fallback writes dispositions the court never entered.

# Extended connectives for tail phrases: reporter tails carry trailing
# qualifiers ("WITH INSTRUCTIONS", "WITHOUT PREJUDICE") the head-zone
# vocabulary deliberately omits.
_TAIL_CONNECT = (
    _DISP_CONNECTIVE
    + r"|WITH|WITHOUT|INSTRUCTIONS|PREJUDICE|COSTS|FURTHER|PROCEEDINGS"
    + r"|ORDERS|OPINION|CONVICTION|SENTENCE|JUDGMENT|IMPOSED|AMENDED"
    # Louisiana appellate-procedure vocabulary. A LA court disposes of the
    # APPEAL as well as the judgment, and those compound sentences read
    # "AFFIRMED. SUSPENSIVE APPEAL DISMISSED, APPEAL MAINTAINED AS A
    # DEVOLUTIVE APPEAL." Without these tokens the phrase run breaks at
    # the first unknown word and the disposition is lost (measured: this
    # class was ~1 in 6 of the all-caps misses).
    + r"|SUSPENSIVE|DEVOLUTIVE|APPEAL|APPEALS|MAINTAINED|ANNULLED"
    + r"|SET|ASIDE|REINSTATED|RENDERED|DECREE|RULING|VERDICT|PART"
    + r"|REHEARING|REMANDED|TRIAL|COURT|BELOW|ASSESSED"
)
_TAIL_PHRASE = (
    r"(?:" + _DISP_VERB + r"|" + _DISP_NOUN_OR_MOD + r")"
    r"(?:[ \t,;.]+(?:" + _DISP_VERB + r"|" + _DISP_NOUN_OR_MOD
    + r"|" + _TAIL_CONNECT + r"))*"
)
# All-caps disposition phrase as a STANDALONE SENTENCE anywhere in the
# tail -- not pinned to end-of-document.
#
# The original \Z anchor (phrase + optional trailing digits + end) was too
# strict and cost ~30% of rows that plainly state their disposition.
# Real Louisiana tails keep going after the disposition: a footnote
# ("AFFIRMED. 1 . This court, in docket number 10-615, ..."), a recusal
# note ("AFFIRMED. GUIDRY, J., recused."), a rules citation ("...been
# DENIED. Uniform Rules - Courts of Appeal, Rule 2-18.7"), or a cc: line.
#
# Still anchored, just to the SENTENCE rather than the document: the
# phrase must start at a line start or after sentence-ending punctuation,
# and must be followed by a period/semicolon or end. That keeps the
# safety property that matters -- an all-caps word inside ordinary prose
# can never match -- while tolerating whatever trails it. Callers take
# the LAST match, which is the operative disposition when a court
# disposes of several things in sequence.
TAIL_ALLCAPS_DISP_RE = re.compile(
    r"(?:^|(?<=[.;!?])|(?<=\n))\s*"
    r"(" + _TAIL_PHRASE + r")"
    r"\s*[.;]",
    re.MULTILINE,
)
# Bare writ-table disposition sentence near the tail. Exact capitalized
# sentence forms only -- "was denied." in prose can never match.
# ---- Prose disposition tier -------------------------------------------
#
# Measured on a 700-row sample of the unfilled corpus (2026-08-19): 12%
# of opinions state their disposition ONLY as prose, with no all-caps
# line anywhere -- dominant in the pre-1980 material but present in every
# era:
#
#   "The judgment is therefore affirmed at defendant's cost."
#   "the judgment appealed from is affirmed; the defendant to pay all
#    the costs of both courts."
#   "it is ordered, adjudged, and decreed that the judgment of the lower
#    court be affirmed, with all costs."
#
# This is the same shape NH's historic tier handles, and it carries the
# same hazard, so it follows the same rules (CLAUDE.md, 2026-07-19):
#
#   1. ANCHORED TO THE FINAL SENTENCE, never a substring search. An
#      unanchored hunt for "affirmed" matches the body's discussion of
#      some OTHER case's outcome and mints a disposition the court never
#      entered -- the exact bug that wrote wrong values on NH.
#   2. Requires JUDGMENT CONTEXT (judgment/decree/ruling/verdict/
#      conviction/sentence/appeal) in the same sentence, so ordinary
#      narrative prose can't qualify.
#   3. TRANSCRIBED, NOT MAPPED. We record the operative verb the court
#      itself used ("affirmed" -> "Affirmed"). That is reading the word
#      on the page, not deciding what an unfamiliar phrase means -- the
#      line NH drew when it refused to map "exceptions overruled" onto
#      "affirmed". If a LA opinion disposes in vocabulary we don't
#      recognize, we leave it blank rather than guess.
_PROSE_CONTEXT = (
    r"judgment|decree|ruling|verdict|conviction|sentence|appeal"
    r"|order|writ|application|judgments"
)
_PROSE_VERB = (
    r"affirmed|reversed|vacated|annulled|amended|remanded|dismissed"
    r"|modified|reinstated|recalled|denied|granted|set aside|quashed"
)
# Split the tail into sentences, then require BOTH context and verb in
# the same one. Sentence-splitting is deliberately crude (period +
# whitespace + capital OR end) because these tails are OCR of century-old
# reporters; precision comes from the two-signal requirement, not from
# perfect segmentation.
PROSE_SENTENCE_RE = re.compile(
    r"(?:^|(?<=[.;!?])\s)\s*([^.;!?]{15,400}?[.;!?])",
    re.MULTILINE,
)
PROSE_VERB_RE = re.compile(r"\b(" + _PROSE_VERB + r")\b", re.IGNORECASE)

# DECRETAL forms only. "Context word somewhere + disposition verb
# somewhere" is NOT enough -- tested against real text it leaked twice:
#
#   "In Smith v. Jones the judgment was affirmed by our brethren, but
#    that case is distinguishable..."          -> stored "Affirmed"
#   "The motion to continue was denied by the trial court, and appellant
#    assigns that ruling as error on appeal."  -> stored "Denied"
#
# Both describe SOMEONE ELSE'S ruling. Storing them would misstate the
# record, which is the precise failure NH's 0.4-confidence body fallback
# produced before it was removed.
#
# So the noun must be the thing being disposed of, ORDERED before the
# verb ("the judgment ... is affirmed"), which drops the second leak --
# there "ruling" appears only AFTER "denied". Two decretal alternatives
# cover the rest: first-person ("we affirm") and the formal Louisiana
# decree ("it is ordered, adjudged, and decreed that ... affirmed").
_PROSE_SUBJECT = r"judgment|judgments|decree|ruling|verdict|conviction|sentence|appeal"
PROSE_DECRETAL_RE = re.compile(
    r"(?:"
    # "the judgment [of the lower court] is/be/was ... affirmed"
    r"\b(?:" + _PROSE_SUBJECT + r")\b[^.;!?]{0,90}?"
    r"\b(?:is|are|be|was|were|stands?)\b[^.;!?]{0,50}?"
    r"\b(?:" + _PROSE_VERB + r")\b"
    r"|"
    # "we affirm" / "we hereby reverse"
    r"\bwe\s+(?:hereby\s+|therefore\s+|accordingly\s+)*"
    r"(?:affirm|reverse|vacate|annul|amend|remand|dismiss|modify|reinstate|recall|deny|grant|quash)\b"
    r"|"
    # "it is ordered, adjudged and decreed that ... affirmed"
    r"\bit\s+is\s+(?:hereby\s+)?(?:ordered|adjudged|decreed)\b[^.;!?]{0,200}?"
    r"\b(?:" + _PROSE_VERB + r")\b"
    r")",
    re.IGNORECASE,
)
# A sentence carrying a case citation is discussing OTHER law, not
# disposing of this appeal. Cheap, high-precision veto.
PROSE_CITATION_VETO_RE = re.compile(
    r"\bv\.\s|\bv\s+[A-Z]|So\.\s?\d|La\.\s?App|\bsupra\b|\bid\.\b",
    re.IGNORECASE,
)
# "in part" qualifiers turn a simple disposition into a compound one
# ("affirmed in part and reversed in part").
PROSE_IN_PART_RE = re.compile(r"\bin\s+part\b", re.IGNORECASE)


def _prose_disposition(tail: str) -> str | None:
    """Extract a disposition from the LAST qualifying prose sentence.

    Returns the transcribed disposition (e.g. ``"Affirmed"``,
    ``"Affirmed in part, reversed in part"``) or None.

    Takes the LAST qualifying sentence, and within it the LAST operative
    verb: Louisiana opinions routinely recite subsidiary rulings before
    the operative one ("the exception is overruled ... the judgment is
    affirmed"), so earlier verbs describe steps, not the outcome. This is
    the same correction AZ's tail fallback needed when a subsidiary
    "we dismiss ... otherwise affirm" was storing "Dismissed".
    """
    best = None
    for m in PROSE_SENTENCE_RE.finditer(tail):
        sentence = m.group(1)
        # Check the citation veto against PRECEDING CONTEXT too, not just
        # the parsed sentence. The splitter breaks on any period, including
        # the one inside "v." -- so "In Smith v. Jones the judgment was
        # affirmed by our brethren" arrives as a fragment starting at
        # "Jones the judgment was affirmed...", with the citation orphaned
        # into the previous fragment and the veto blind to it. Widening the
        # window to the 120 chars before the sentence puts the citation back
        # in view. (Found by a false-positive test, not by reading code.)
        ctx = tail[max(0, m.start(1) - 120):m.end(1)]
        if PROSE_CITATION_VETO_RE.search(ctx):
            continue
        if not PROSE_DECRETAL_RE.search(sentence):
            continue
        verbs = PROSE_VERB_RE.findall(sentence)
        if not verbs:
            continue
        best = (sentence, verbs)
    if best is None:
        return None

    sentence, verbs = best
    # Compound: two DIFFERENT verbs plus an "in part" qualifier.
    lowered = [v.lower() for v in verbs]
    unique = list(dict.fromkeys(lowered))
    if len(unique) >= 2 and PROSE_IN_PART_RE.search(sentence):
        a, b = unique[0], unique[1]
        return f"{a.capitalize()} in part, {b} in part"
    # Otherwise the LAST verb is the operative one.
    return unique[-1].capitalize()


TAIL_WRIT_SENTENCE_RE = re.compile(
    r"(?:^|[.;]\s+)"
    r"(Writ\s+[Dd]enied|Writ\s+[Gg]ranted|Writ\s+[Rr]ecalled"
    r"|Denied|Granted|Stay\s+[Dd]enied|Stay\s+[Gg]ranted"
    r"|Reconsideration\s+[Dd]enied)"
    r"\.(?!\w)",
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
# GREEDY capture, deliberately: the lazy version stopped at the FIRST
# ", J." -- on "BEFORE: WHIPPLE, C. J., PENZATO AND HESTER, JJ." it
# captured "WHIPPLE, C." (minting a 961-vote judge named "C" from the
# Chief Judge marker) and silently DROPPED the rest of the panel
# (2026-08-25). Greedy backtracks from the line end to the final
# "JJ."; the char class carries no \n so it can't cross lines.
# \n is allowed inside the capture (bounded at 300 chars) because long
# 1st Cir panels WRAP: "BEFORE: GUIDRY, C.J., HOLDRIDGE, WOLFE, MILLER,
# AND\n<...>, JJ." -- the old single-line class silently returned no
# panel at all for every wrapped line (part of the 83%-authorless-panel
# stat). Lowercase body text ends the class, so the capture can't run
# into prose; the ", J{1,2}." terminator anchors the backtrack.
COA_BEFORE_RE = re.compile(
    r"BEFORE\s*:\s*([A-Z][A-Z, .'\-\n]{0,300}),?\s+J{1,2}\s*\.",
    re.MULTILINE)

# Reporter-era variant: "Before LEAR, CARTER and LANIER, JJ." --
# titlecase "Before", no colon, lowercase "and", names ALL-CAPS, often
# mid-line in flowing text. Two terminators, tried in order: the
# double-J "JJ." (multi-judge panels; lazy, and immune to an inline
# "C.J." marker, which carries no whitespace-J-J sequence) and the
# single "J." (one-judge writ panels). The consumer keeps only
# ALL-CAPS tokens >=3 chars, so lowercase prose, titlecase attorney
# names, and the "and" connective can never enter the panel.
BEFORE_TITLECASE_JJ_RE = re.compile(
    r"\bBefore\s+([A-Z][^\n]{0,200}?),?\s+JJ\s*\.")
BEFORE_TITLECASE_J_RE = re.compile(
    r"\bBefore\s+([A-Z][^\n]{0,120}?),?\s+J\s*\.")

# Role/marker tokens that ride inside panel name lists and must never
# become judges: "WHIPPLE, C. J., PENZATO" carries the Chief Judge
# marker as a list element; 5th Cir panels append "Pro Tempore" /
# "Ad Hoc" after a name. Compared lowercase, punctuation stripped.
_PANEL_ROLE_TOKENS = {
    "c", "j", "cj", "jj", "c j",
    "pro tempore", "pro tem", "ad hoc",
    "judge pro tempore", "judge ad hoc", "judges",
    # Generational suffixes ride the lists as standalone tokens in
    # reporter text ("EDWARD A. DUFRESNE, JR., ...") and once minted a
    # judge named "Iii".
    "jr", "sr", "ii", "iii", "iv",
}


def _norm_panel_token(chunk: str) -> str:
    """Normalize a panel-list chunk for the role-token check.

    Lowercase, periods dropped, whitespace collapsed -- so "C. J.",
    "C.J." and "c j" all normalize to "c j" / "cj" family entries in
    _PANEL_ROLE_TOKENS.
    """
    return " ".join(chunk.replace(".", " ").lower().split())


def _space_name_particles(chunk: str) -> str:
    """Insert the missing space in fused St./Ste. name particles.

    Reporter text renders "EMILE ST.PIERRE" with no space; left fused,
    the surname token becomes "st.pierre" and can never match a judge
    row whose canonical surname is "Pierre" -- resolve_judges then
    mints a shadow "St.pierre" judge (it did, twice, 2026-08-25).
    """
    return re.sub(r"\b(St|Ste)\.(?=[A-Za-z])", r"\1. ", chunk,
                  flags=re.IGNORECASE)


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

        # Tail fallback (both courts): reporter-style CL bulk texts put
        # the disposition at the END, not in a header zone. Only fires
        # when the head passes found nothing, so modern-layout parses
        # are untouched. See the tail-tier comment above the regexes.
        if not result.disposition:
            stripped = raw_text.rstrip()
            tail = stripped[-600:]

            # Tier A (0.80): all-caps disposition sentence. Take the LAST
            # match -- a court that disposes of several things in sequence
            # ("AFFIRMED. SUSPENSIVE APPEAL DISMISSED, APPEAL MAINTAINED
            # ...") states the operative one last, and the sentence anchor
            # means trailing footnotes/recusals/cc: lines no longer hide it.
            last_caps = None
            for m in TAIL_ALLCAPS_DISP_RE.finditer(tail):
                last_caps = m
            if last_caps:
                phrase = " ".join(last_caps.group(1).split()).rstrip(" ,;.")
                # Require a real vocabulary VERB -- rejects a stray "SEE",
                # "NOT", or a lone connective surviving the phrase run.
                if re.search(_DISP_VERB, phrase):
                    result.disposition = (
                        phrase[:1].upper() + phrase[1:].lower())
                    result.confidence["disposition"] = 0.8

            # Tier B (0.80): writ-table entry ending in a bare "Denied." /
            # "Granted." sentence. The writ-context gate is checked against
            # a MUCH wider window than the sentence search: a long per
            # curiam pushes the "applying for writs" recitation thousands
            # of characters above the disposition, and a 600-char gate
            # silently skipped every one of those (LA Supreme is ~199K
            # rows, mostly writ dispositions, so this class is large).
            if not result.disposition and re.search(
                    r"\b(writ|writs|applying|application)\b",
                    stripped[-6000:], re.IGNORECASE):
                last = None
                for m in TAIL_WRIT_SENTENCE_RE.finditer(tail[-300:]):
                    last = m
                if last:
                    phrase = " ".join(last.group(1).split())
                    result.disposition = (
                        phrase[:1].upper() + phrase[1:].lower())
                    result.confidence["disposition"] = 0.8

            # Tier C (0.75): prose. Lowest tier and last resort, because
            # it reads ordinary sentences rather than a formatted line --
            # see _prose_disposition for the anchoring + transcription
            # rules that keep it honest.
            if not result.disposition:
                prose = _prose_disposition(stripped[-900:])
                if prose:
                    result.disposition = prose
                    result.confidence["disposition"] = 0.75

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
                    # Cut at the end of the panel SENTENCE. Reporter-era
                    # text flows straight into the opinion body ("...and
                    # WALTER J. ROTHSCHILD. MARION F. EDWARDS, Judge.
                    # Defendant, Robert Bolton, appeals...") and an
                    # uncut blob minted PARTY NAMES as judges (the LA
                    # "Defendant"/"Bolton" leak, 2026-08-25). A period
                    # ends the sentence when the word before it has 2+
                    # letters and isn't a generational suffix or the
                    # St./Ste. name particle -- single-letter initials
                    # ("Marc E. Johnson") keep the list going.
                    for dot in re.finditer(r"([A-Za-z'\-]+)\.", names_blob):
                        w = dot.group(1)
                        if len(w) >= 2 and w.lower() not in (
                                "jr", "sr", "ii", "iii", "iv", "st", "ste"):
                            names_blob = names_blob[:dot.end() - 1]
                            break
                    # Split on ", and" / "," / " and " (word-boundary
                    # "and" so it doesn't split "Alexander")
                    for chunk in re.split(
                            r",\s+and\s+|,\s*|\s+and\s+", names_blob):
                        chunk = _space_name_particles(
                            " ".join(chunk.split()).rstrip("."))
                        if _norm_panel_token(chunk) in _PANEL_ROLE_TOKENS:
                            continue
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
                        chunk = _space_name_particles(
                            chunk.strip().strip(".").strip())
                        # Drop role/marker tokens ("C. J." rides inside
                        # the list on Chief Judge panels) and any
                        # single-letter residue -- see _PANEL_ROLE_TOKENS.
                        if _norm_panel_token(chunk) in _PANEL_ROLE_TOKENS:
                            continue
                        if len(chunk.replace(".", "").replace(" ", "")) < 2:
                            continue
                        if chunk and chunk.replace("-", "").replace("'", "").isalpha():
                            surname = _titlecase_caps(chunk)
                            if surname not in panel:
                                panel.append(surname)
            # Reporter-era titlecase form, no colon: "Before LEAR, CARTER
            # and LANIER, JJ." (1980s-90s West reports; names ALL-CAPS,
            # connective lowercase). The uppercase BEFORE: regex misses it
            # entirely, which read as "no panel" for a whole era -- and
            # nearly got 1,065 REAL Lanier votes deleted when a vote-level
            # cleanup trusted partial extraction as complete (2026-08-25).
            # Window-based: take the text from "Before" to the first
            # "J{1,2}." terminator, keep only ALL-CAPS tokens (>=3 chars,
            # so "and"/prose can't enter), filter role tokens.
            if not panel:
                wm = (BEFORE_TITLECASE_JJ_RE.search(raw_text[:8000])
                      or BEFORE_TITLECASE_J_RE.search(raw_text[:8000]))
                if wm:
                    blob = wm.group(1)
                    # Pro-tem tail: "Before CARTER and PITCHER, JJ., and
                    # CRAIN, J. Pro Tem." -- the designated judge rides
                    # AFTER the JJ. terminator the lazy match stops at,
                    # and dropping it refuted ~130 REAL pro-tem votes in
                    # the vote-level cleanup's dry run (2026-08-25).
                    cont = re.match(
                        r"\s*,?\s*and\s+([A-Z][^\n]{0,80}?),?\s+"
                        r"J{1,2}\s*\.?,?\s*[Pp]ro\s*[Tt]em",
                        raw_text[wm.end():wm.end() + 120])
                    if cont:
                        blob += ", " + cont.group(1)
                    # Split into per-judge SEGMENTS first, then keep only
                    # the LAST caps token of each -- 2nd Cir wrote full
                    # names in Before lines ("Before JASPER E. JONES,
                    # FRED W. JONES, Jr., and ..."), and a flat token
                    # scan minted the FIRST names as judges ("Fred" 847
                    # votes, "Jasper" 724 -- found 2026-08-25).
                    # (?:Mc|Mac)? -- "McDONALD"/"McKAY" carry an internal
                    # lowercase letter; a plain ALL-CAPS token filter
                    # never extracted them, which read as Mc-judges
                    # absent from their own panels (127 real McClendon
                    # votes nearly refuted).
                    for seg in re.split(r",\s*(?:and\s+)?|\s+and\s+", blob):
                        toks = [
                            t for t in re.findall(
                                r"\b(?:Mc|Mac)?[A-Z][A-Z.'\-]{2,}", seg)
                            if _norm_panel_token(
                                _space_name_particles(t.rstrip(".,")))
                            not in _PANEL_ROLE_TOKENS
                        ]
                        if not toks:
                            continue
                        tok = _space_name_particles(toks[-1].rstrip(".,"))
                        surname = _titlecase_caps(tok)
                        if surname and surname not in panel:
                            panel.append(surname)
        result.panel = panel
        if panel:
            result.confidence["panel"] = 0.8

        return result
