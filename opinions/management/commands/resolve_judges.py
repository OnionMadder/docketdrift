"""Resolve judges + auto-create PanelVote rows from opinion text.

The CL bulk load brought in ~3,610 PanelVote rows -- only ~6% of the
60K corpus has structured panel data. The remaining ~57K opinions DO
contain panel info in their raw_text (typical MN format: "Filed June 1,
2026 / Affirmed / Larson, Judge" + "Considered and decided by Larson,
Judge; Bjorkman, Judge; and Wheelock, Judge"), they just never got
matched to Judge model rows.

This command does that match: parses each opinion, extracts the byline
author + the panel list, looks up each name against ``state``'s Judge
table by last-name, and creates ``PanelVote`` rows with the appropriate
vote_type. Idempotent via ``get_or_create`` on the existing
``(opinion, judge)`` unique constraint -- re-runs only ever ADD votes,
never modify existing ones (except a Pass-1 upgrade from MAJORITY_JOIN
to MAJORITY_AUTHOR when the same judge turns out to be the byline
author).

Match strategy: last-name only, case-insensitive. Ambiguous last names
(multiple judges with the same surname) are skipped + counted in the
summary so the editor can disambiguate manually. Acceptable miss rate
for v1 -- the alternative is a per-judge alias table.

Usage::

    python manage.py resolve_judges            # full MN pass
    python manage.py resolve_judges --state MN --limit 500 --dry-run
    python manage.py resolve_judges --since 2020-01-01  # only recent

Cost: regex-only, no API calls. ~10-20 minutes for the full corpus
since each opinion's raw_text gets re-parsed.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import connection, models
from django.utils.text import slugify

from opinions.models import Judge, Opinion, PanelVote


# Strip the role suffix (", Judge" / ", Justice" / etc.) off a byline.
_ROLE_SUFFIX_RE = re.compile(
    r",\s*(?:Chief\s+)?(?:Judge|Justice|J\.|C\.J\.)\.?\s*$",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------
# Generic fallback byline extractor.
#
# For states without a registered state-specific parser, we still want
# to learn who authored + sat on the panel for each opinion. NH and AZ
# (and any future state that doesn't have a parser yet) hit this path
# until their state-specific parser is built.
#
# Pattern that catches the bulk of NH appellate opinions:
#   MACDONALD, C.J., and COUNTWAY and GOULD, JJ., concurred.
#   DONOVAN, COUNTWAY, and GOULD, JJ., concurred.
#   COUNTWAY and GOULD, JJ., concurred; TEMPLE, J., specially assigned ...
#
# Heuristic: scan the LAST ~2KB of raw_text (opinions sign off at the
# bottom) for the surnames immediately preceding "C.J.", "J.", or "JJ.,"
# tags followed by "concurred". The first surname tagged "C.J." (Chief
# Justice) is treated as the author if present -- that's the convention
# in single-author opinions where the chief signs first. Otherwise the
# panel is treated as per-curiam (all-join, no distinct author).
# ----------------------------------------------------------------------

# Surname token: starts with an uppercase letter (or accented uppercase
# letter to handle names like "VÁSQUEZ" / "Vásquez"), 3+ chars total,
# allows internal mixed case so both "MACDONALD" (NH all-caps style) and
# "Pelander" (AZ mixed-case style) match. Hyphens and apostrophes
# permitted for names like "O'Brien" / "Smith-Jones".
#
# `À-ÿ` covers Latin-1 supplement (À-ÿ) which catches the
# common accented characters in justice surnames (Vásquez, Núñez, etc.)
# without dragging in arbitrary unicode.
_SURNAME = r"[A-ZÀ-ß][A-Za-zÀ-ÿ\-']{2,}"

# Run of "<S1>, <S2>, and <S3>, JJ.," or "<S>, J.," patterns near the
# disposition footer. Captures the comma-separated surname list before
# the role suffix. The optional ``chief`` prefix catches "<X>, C.J., and"
# at the start of a mixed signoff like:
#   MACDONALD, C.J., and COUNTWAY and GOULD, JJ., concurred.
# where the Chief Justice has their own inline C.J. marker before the
# remaining JJ.-tagged panel members.
#
# Also accepts AZ-style "concurring" (Court of Appeals convention) and
# "joined" (rare older formats) as alternatives to "concurred".
_PANEL_GROUP_RE = re.compile(
    # Inline chief / presiding signer at the start of the byline:
    # NH style: "MACDONALD, C.J., and ..."
    # AZ-CtApp style: "Vasquez, P.J., and ..."   (Presiding Judge)
    rf"(?:\b(?P<chief>{_SURNAME}),\s*(?:C\.J\.|P\.J\.),\s*and\s+)?"
    # Panel surname list. The separator between two surnames MUST contain a
    # comma or "and" -- it is NOT allowed to be whitespace-only. That matters
    # for more than tidiness: with a nullable separator, `S(?:sepS)*` can
    # partition any run of Capitalized Words a combinatorial number of ways,
    # and when the trailing `JJ., concurred` role suffix is absent (the common
    # case) the engine backtracks through all of them -- catastrophic ReDoS
    # that hung on a whole era of 2017-2019 AZ opinions and CPU-culled the
    # command mid-run. Requiring a real delimiter makes the split unambiguous
    # and the match linear. Real panel lists always use commas/"and" anyway.
    rf"\b(?P<panel>(?:{_SURNAME})(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+)(?:{_SURNAME}))*)"
    rf",?\s+(?P<role>C\.J\.|P\.J\.|JJ?\.)\s*,?\s*(?:concurred|concurring|join(?:ed)?)\b"
)

# NH dissent-footer continuation. In opinions with a dissent, the
# footer-style signoff continues past the majority's "concurred." with
# an explicit dissenter line:
#   MACDONALD, C.J., and DONOVAN and COUNTWAY, JJ., concurred;
#   BASSETT, J., dissented.
# Each dissenter gets a DISSENT_AUTHOR vote -- they wrote their own
# dissenting opinion that gets a separate section header
# ("BASSETT, J., dissenting.") earlier in the body. We use this footer
# pattern rather than the body header because the footer is more
# uniform and unambiguous (the body header can show up inside cited
# quotations from OTHER cases).
_DISSENT_FOOTER_RE = re.compile(
    rf"\b(?P<name>{_SURNAME}),\s*J\.,?\s+dissented\b",
)

# Cross-court justices to STOPLIST out of the footer path.
#
# The NH-style footer pattern ("SURNAME, JJ., concurred") is structurally
# IDENTICAL to a parenthetical citation of another court's lineup -- e.g. an
# AZ or NH opinion quoting "(Scalia, Thomas, and Gorsuch, JJ., concurring)"
# from a SCOTUS opinion. Measured across the whole corpus, NOTHING structural
# separates the two: verb form (real AZ signoffs use the present participle
# "concurring" too), position, and last-match-in-document all fail. The only
# reliable discriminator is the identity of the name -- a judge of THIS court
# vs. a cited out-of-court (US Supreme Court) justice.
#
# This set was built mechanically: a comprehensive list of SCOTUS justice
# surnames, MINUS any that collide with a real judge in the MN/NH/AZ rosters
# (that subtraction is what keeps genuine locals like NH's Souter -- an actual
# NH justice before SCOTUS -- and AZ's Miller/Stevens and MN's Murphy). It is
# applied ONLY to the weak footer path, never to the corroborated top-of-
# opinion byline block, so a real "Judge Roberts delivered the opinion of the
# Court" is still captured. If a NEW state is onboarded, RE-VERIFY this list
# against its roster before trusting it (a real Judge Marshall/Kennedy/etc.
# elsewhere would need removing here).
_CROSS_COURT_JUSTICES = frozenset({
    "alito",
    "barrett", "black", "blackmun", "bradley", "brandeis", "brennan", "brewer",
    "breyer", "burton", "butler", "byrnes", "cardozo", "chase", "clark",
    "douglas", "field", "fortas", "frankfurter", "fuller", "ginsburg",
    "goldberg", "gorsuch", "gray", "harlan", "holmes", "hughes", "jackson",
    "kagan", "kavanaugh", "kennedy", "marshall", "mcreynolds", "minton",
    "oconnor", "pitney", "powell", "reed", "rehnquist", "roberts", "rutledge",
    "sanford", "scalia", "sotomayor", "stewart", "stone", "story", "sutherland",
    "taft", "thomas", "vinson", "waite", "warren", "white", "whittaker",
})

# Non-name tokens the strict-caps name regexes can still capture as a
# "surname" -- connectives, role words, procedural verbs, party/structural
# words, plus a few confirmed junk captures (2026-08-07 AZ audit: "And",
# "Appel", "Opinion", "State", ...). NONE collide with a real MN/NH/AZ judge
# surname. Applied at every capture point in _extract_generic_byline.
_NON_NAME_TOKENS = frozenset({
    "a", "an", "and", "or", "the", "of", "to", "by", "for", "with", "in",
    "which", "per", "also", "we", "did", "not", "sit", "at",
    "judge", "judges", "justice", "justices", "chief", "vice", "presiding",
    "associate", "curiam", "panel", "court", "courts", "opinion", "opinions",
    "appeal", "appeals", "appel", "appellant", "appellee", "state",
    "concurred", "concurring", "dissented", "dissenting", "joined", "join",
    "authored", "delivered", "affirmed", "reversed",
    "silent", "trade", "one", "hon", "ini",
    # 2026-08-25 LA audit: party words + role riders the LA parser's
    # panel paths leaked ("Defendant" 436 votes, "Tempore" 65 from
    # "Pro Tempore", "Curiam" 254 from a per-curiam author string).
    "defendant", "plaintiff", "tempore", "tem", "hoc",
    # Generational suffixes are never surnames; a standalone "III" in a
    # reporter panel list once minted a judge named "Iii" (89+ votes).
    "iii", "iv", "jr", "sr",
})


def _valid_surname(tok: str) -> bool:
    """Reject non-name tokens the strict-caps name regex can capture. A real
    surname is >=3 letters, contains an alphabetic character, and is not a
    known non-name word."""
    t = tok.strip().lower().rstrip(",.;'")
    return len(t) >= 3 and t not in _NON_NAME_TOKENS and any(c.isalpha() for c in t)


def _inside_open_paren(text: str, pos: int, window: int = 150) -> bool:
    """True if ``pos`` sits inside an unclosed ``(`` within the preceding
    window -- i.e. the surname is part of a parenthetical citation
    ``(Name, J., concurring)`` rather than a bare end-of-opinion panel
    signoff. This is the reliable discriminator between a CITED out-of-court
    judge and a real panelist: citations are parenthetical, signoffs are not.
    It catches every cross-court leak generically (circuit judges, sister-state
    justices), not just the SCOTUS surnames in _CROSS_COURT_JUSTICES -- which is
    what let Kozinski/Dietzen/Titone/Tjoflat through into the AZ roster.
    A closed ``(...)`` before ``pos`` (e.g. a "(1993)" year cite) does not
    trip it: rfind of the later ``)`` wins."""
    pre = text[max(0, pos - window):pos]
    return pre.rfind("(") > pre.rfind(")")


# AZ-style byline lives at the TOP of the opinion, not the bottom. Two
# distinct conventions, both handled here:
#
# 1. AZ Court of Appeals -- mixed-case "Judge", singular per name:
#      Presiding Judge David B. Gass delivered the decision of the court,
#      in which Judge Michael J. Brown and Judge Andrew J. Becke joined.
#      Vice Chief Judge Eppich authored the opinion of the Court, in which
#      Presiding Judge Vasquez and Chief Judge Staring concurred.
#
# 2. AZ Supreme Court -- ALL-UPPERCASE "JUSTICE", plus the panel often
#    shares one plural "JUSTICES" prefix over a comma-separated list:
#      CHIEF JUSTICE TIMMER authored the Opinion of the Court, in which
#      VICE CHIEF JUSTICE LOPEZ, JUSTICES BOLICK, BEENE, KING, and CRUZ
#      joined.
#    Author is the first named role; panel = everything in the joined list.
#
# Two-step: find the "<author>... authored/delivered... in which <list>
# concurred/joined" block, then enumerate via TWO sub-regexes -- a singular
# "<role> <name>" matcher (catches CoA and AZ-Supreme's leading roles) and a
# plural "JUSTICES <name>, <name>, ..." matcher (catches AZ-Supreme's
# panel-list shorthand).
#
# CRITICAL: name capture must be CASE-SENSITIVE so lowercase verbs like
# "joined" / "authored" don't slip into a name slot. The enclosing role
# prefix and verbs are CASE-INSENSITIVE so both "Chief Judge" (CoA) and
# "CHIEF JUSTICE" (Supreme) match. We mix these flags via Python's
# `(?-i:...)` inline scoping: the outer regex uses re.IGNORECASE, but the
# name capture group disables IGNORECASE locally.
_AZ_ROLE_PREFIX_CI = r"(?:Presiding\s+|Vice\s+Chief\s+|Chief\s+|Vice\s+)?(?:Judge|Justice)"
# Strict name: each word must start with an uppercase (or accented uppercase)
# letter. Allow internal periods (initials like "B."), apostrophes, hyphens.
_AZ_NAME_STRICT = (
    r"[A-ZÀ-ß][A-Za-zÀ-ÿ.'\-]+"           # required first word
    r"(?:\s+[A-ZÀ-ß][A-Za-zÀ-ÿ.'\-]+){0,3}"  # up to 3 additional words
)
_AZ_BYLINE_BLOCK_RE = re.compile(
    # Bounded block: "<role> <name> authored/delivered ... OF THE COURT ...
    # in which ... concurred/joined". DOTALL because it spans 2-3 lines.
    #
    # The "the Court('s)" anchor is load-bearing, not decoration: without it
    # this block also matched an AZ opinion DESCRIBING another court's
    # authorship -- "Justice Scalia authored a dissent, in which Justice Thomas
    # joined" -- and minted the cited SCOTUS justices as AZ panel members (with
    # bogus MAJORITY_AUTHOR votes). The real AZ byline always attributes THIS
    # court's own opinion, in one of two phrasings: "delivered the opinion OF
    # THE COURT, in which ..." OR the possessive "delivered the COURT'S opinion,
    # in which ..." (the form recent CoA judges McMurdie/Williams use). A body
    # citation to another court's opinion almost never fills that slot.
    rf"\b{_AZ_ROLE_PREFIX_CI}\s+(?-i:{_AZ_NAME_STRICT})"
    rf"\s+(?:authored|delivered)\s+[\s\S]{{0,40}}?"
    rf"(?:(?:of|for)\s+the\s+court\b|the\s+court['’]s)"
    rf"[\s\S]{{0,120}}?"
    rf"in\s+which\s+[\s\S]{{0,400}}?\b(?:concurred|joined)\b",
    re.IGNORECASE | re.DOTALL,
)
# Singular: "<role> <name>" -- catches CoA's mixed-case "Judge Brown" and
# AZ-Supreme's "CHIEF JUSTICE TIMMER" / "VICE CHIEF JUSTICE LOPEZ" /
# "JUSTICE KING". Role prefix matches case-insensitively; name stays
# case-sensitive.
_AZ_NAMED_SINGULAR_RE = re.compile(
    rf"\b{_AZ_ROLE_PREFIX_CI}\s+((?-i:{_AZ_NAME_STRICT}))",
    re.IGNORECASE,
)
# Plural: "JUSTICES <name>, <name>, ..., and <name>" -- AZ Supreme shares
# one "JUSTICES" prefix over a comma-separated panel list, optionally with
# an Oxford-comma "and" before the final name. Each name in the list stays
# case-sensitive so " joined" / lowercase prose doesn't get slurped.
_AZ_NAMED_PLURAL_RE = re.compile(
    rf"\bJUSTICES\s+((?-i:{_AZ_NAME_STRICT})(?:\s*,\s*(?:and\s+)?(?-i:{_AZ_NAME_STRICT}))*)",
    re.IGNORECASE,
)

# MN caption-style separate-opinion attributions. MN opinions carry the
# panel split in caption lines right under the disposition line:
#     Segal, Judge∗
#     Dissenting, Harris, Judge
#     Concurring in part, dissenting in part, Bratvold, Judge
#     Dissenting, Moore, III, McKeig, Hennesy, Justices    (Supreme, multi)
#     Concurring specially, Ross, Judge
# Measured 2026-08-16 across post-2015 opinions containing "dissent":
# the Dissenting caption appears in 34%, concur-in-part in 10%, plain
# Concurring in 4%; body headers ("HARRIS, Judge (dissenting)") reach the
# caption zone in <1% -- so the caption lines are the extraction surface.
# Alternation order matters: the longest form must be tried before the
# "Concurring" prefix that is its substring.
_MN_SEPARATE_CAPTION_RE = re.compile(
    r"^[ \t]*(?P<kind>Concurring in part, dissenting in part"
    r"|Dissenting"
    r"|Concurring(?: specially)?)"
    r",[ \t]*(?P<names>[A-ZÀ-ß][^\n]*)$",
    re.MULTILINE,
)
# Tokens to drop when splitting an MN caption name list: role words +
# generational suffixes (the caption lists surnames with occasional
# "III" suffixes and a trailing "Judge"/"Justices" role tag).
_MN_CAPTION_DROP = frozenset({
    "judge", "judges", "justice", "justices", "chief",
    "jr", "sr", "ii", "iii", "iv",
})


def _mn_caption_names(blob: str) -> list[str]:
    """Parse an MN caption name list ("Harris, Judge" / "Moore, III,
    McKeig, Hennesy, Justices") into lowercased surnames."""
    out: list[str] = []
    for tok in blob.split(","):
        t = tok.strip().strip(".∗* \t")
        if not t:
            continue
        if t.lower().rstrip(".") in _MN_CAPTION_DROP:
            continue
        # Caption entries are bare surnames; keep the last word so a stray
        # "Moore III" (unsplit suffix) still resolves to "moore".
        last = t.split()[-1].rstrip(",.;'")
        if last.lower() in _MN_CAPTION_DROP:
            continue
        if _valid_surname(last):
            out.append(last.lower())
    return out


# AZ prose-style separate-opinion attributions, adjacent to the byline
# block at the top of the opinion:
#     ... in which Presiding Judge Brearcliffe concurred and Judge
#     Eckerstrom dissented.
#     Judge Anni Hill Foster concurred in part and dissented in part.
#     * CHIEF JUSTICE TIMMER dissented, joined by JUSTICE KING.
#     Justice Bolick specially concurred.
# Measured frequencies (post-2015 opinions containing "dissent"):
# "<role> <name> dissented" 21%, concur-in-part 9%, specially-concurred
# 2%, "dissented, joined by" once (the joiner is picked up by nothing --
# accepted miss at n=1). The plain "dissented" pattern cannot swallow the
# concur-in-part form: the strict-caps name capture stops at the
# lowercase "concurred", which then fails the required "dissented" verb.
_AZ_PART_DISSENT_RE = re.compile(
    rf"\b{_AZ_ROLE_PREFIX_CI}\s+((?-i:{_AZ_NAME_STRICT}))\s+concurred\s+in\s+part\s+and\s+dissented\s+in\s+part",
    re.IGNORECASE,
)
_AZ_DISSENTED_RE = re.compile(
    rf"\b{_AZ_ROLE_PREFIX_CI}\s+((?-i:{_AZ_NAME_STRICT}))\s+dissented\b",
    re.IGNORECASE,
)
_AZ_SPECIAL_CONCUR_RE = re.compile(
    rf"\b{_AZ_ROLE_PREFIX_CI}\s+((?-i:{_AZ_NAME_STRICT}))\s+"
    rf"(?:specially\s+concurred|concurred\s+specially|concurred\s+in\s+part\b(?!\s+and\s+dissented))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GenericByline:
    """Output of the generic byline extractor."""
    author_last: str | None
    panel_last: list[str]
    dissenter_last: list[str]
    concurrer_last: list[str]
    raw_matches: list[str]  # for debug / log inspection


def _extract_generic_byline(raw_text: str) -> GenericByline:
    """Extract author + panel last-names from any-state opinion text.

    Returns lowercased last-names ready to match against last_name_map.
    Falls back gracefully (empty author + empty panel) on text that
    doesn't follow either of the two supported conventions:

    - NH-style footer concurrence (``X, JJ., concurred.``) -- scanned in
      the LAST ~2KB of raw_text.
    - AZ-style top-of-opinion byline (``Judge X authored the opinion of
      the Court, in which Judge Y and Judge Z joined``) -- scanned in
      the FIRST ~4KB of raw_text.
    """
    if not raw_text:
        return GenericByline(None, [], [], [], [])

    author_last: str | None = None
    all_panel: list[str] = []
    dissenter_lasts: list[str] = []
    concurrer_lasts: list[str] = []
    raw_matches: list[str] = []

    # --- AZ-style top-of-opinion byline ---
    # Caption is typically within the first 3-4KB (preamble + counsel
    # block + "OPINION" header + first sentence of the byline). Scan
    # the first 5KB to be safe.
    head = raw_text[:5000]

    def _last(name: str) -> str:
        parts = name.strip().split()
        if not parts:
            return ""
        tok = parts[-1]
        return tok.lower().rstrip(",.;'") if _valid_surname(tok) else ""

    for block in _AZ_BYLINE_BLOCK_RE.finditer(head):
        raw_matches.append(block.group(0)[:200])
        block_text = block.group(0)

        # Two-pass extraction:
        # 1) Singular "<role> <name>" matches (CoA + AZ-Supreme leading roles)
        # 2) Plural "JUSTICES <name>, <name>, ..." panel lists (AZ Supreme)
        # Preserve textual order within each pass so the FIRST captured
        # judge is the byline author. Dedupe across both passes after.
        in_block_judges: list[str] = []

        for m in _AZ_NAMED_SINGULAR_RE.finditer(block_text):
            last = _last(m.group(1))
            if last:
                in_block_judges.append(last)

        for m in _AZ_NAMED_PLURAL_RE.finditer(block_text):
            names_blob = m.group(1)
            # Split on commas (with optional "and") and on bare " and ".
            for nm in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", names_blob):
                last = _last(nm.strip())
                if last:
                    in_block_judges.append(last)

        if not in_block_judges:
            continue

        # First named judge = author; the rest = panel members. Dedupe
        # preserving order (a judge captured by both passes counts once).
        seen_in_block: set[str] = set()
        ordered: list[str] = []
        for j in in_block_judges:
            if j not in seen_in_block:
                seen_in_block.add(j)
                ordered.append(j)

        if author_last is None:
            author_last = ordered[0]
        for nm in ordered[1:]:
            all_panel.append(nm)

    # --- AZ prose-style dissent / special concurrence (same head) ---
    # These sentences sit adjacent to the byline block ("...concurred and
    # Judge Eckerstrom dissented." / "* CHIEF JUSTICE TIMMER dissented,
    # joined by JUSTICE KING."). Paren guard + cross-court stoplist keep
    # cited out-of-court judges ("...(Hurwitz, J., dissenting)") and body
    # prose about OTHER courts' dissents out. Order matters: the
    # concur-in-part pattern must be collected before the plain
    # "dissented" scan runs, but the two cannot double-capture one name
    # (the strict-caps name capture stops at the lowercase verb).
    def _collect_az(pattern: re.Pattern, dest: list[str]) -> None:
        for m in pattern.finditer(head):
            if _inside_open_paren(head, m.start()):
                continue
            last = _last(m.group(1))
            if last and last not in _CROSS_COURT_JUSTICES and last not in dest:
                dest.append(last)
                raw_matches.append(m.group(0)[:120])

    _collect_az(_AZ_PART_DISSENT_RE, dissenter_lasts)
    _collect_az(_AZ_DISSENTED_RE, dissenter_lasts)
    _collect_az(_AZ_SPECIAL_CONCUR_RE, concurrer_lasts)

    # --- MN caption-style separate opinions (same head) ---
    # Caption lines under the disposition: "Dissenting, Harris, Judge" /
    # "Concurring in part, dissenting in part, Bratvold, Judge" /
    # "Dissenting, Moore, III, McKeig, Hennesy, Justices". A partial
    # dissent counts as a dissent (the judge broke with part of the
    # judgment and wrote separately); a plain/special concurrence is a
    # concurrence.
    for m in _MN_SEPARATE_CAPTION_RE.finditer(head):
        kind = m.group("kind")
        names = _mn_caption_names(m.group("names"))
        if not names:
            continue
        raw_matches.append(m.group(0)[:120])
        dest = dissenter_lasts if "issent" in kind else concurrer_lasts
        for ln in names:
            if ln not in _CROSS_COURT_JUSTICES and ln not in dest:
                dest.append(ln)

    # --- NH-style footer concurrence ---
    # Concentrate the search on the last 8KB -- panel lists are at the
    # footer, never the body. For unanimous opinions the footer sits in
    # the last few hundred bytes, but opinions with a dissent push the
    # majority footer back behind the (typically 4-10KB) dissent body,
    # so the window has to be generous. ``_PANEL_GROUP_RE`` requires the
    # explicit ", JJ., concurred" / ", J., concurred" anchor, so this
    # wider window doesn't admit false positives from majority prose
    # like "ROBERTS sued LARSON".
    tail = raw_text[-8000:]

    for m in _PANEL_GROUP_RE.finditer(tail):
        # A "SURNAME, J., concurring" that sits inside a parenthetical is a
        # CITATION to another court's opinion ("...472 (Kozinski, J.,
        # concurring)"), not this court's panel signoff. Real signoffs are
        # bare sentences. This is name-agnostic, so it catches circuit and
        # sister-state judges the SCOTUS-only _CROSS_COURT_JUSTICES stoplist
        # never could -- the root cause of the 2026-08-07 AZ leak cull.
        if _inside_open_paren(tail, m.start()):
            continue
        raw_matches.append(m.group(0))
        chief = m.group("chief")
        names_blob = m.group("panel")
        role = m.group("role")
        # The inline Chief Justice (when present) is the signer/author of
        # the opinion -- record + remember separately from the panel. Skip it
        # if it's a non-name token or a stoplisted cross-court justice (a cited
        # "(Roberts, C.J., ...)" must not become this opinion's author).
        if chief and _valid_surname(chief) and chief.lower() not in _CROSS_COURT_JUSTICES:
            author_last = chief.lower()
        # Split on " and " and "," to enumerate panel surnames. The
        # _SURNAME regex requires uppercase + 3+ letters, so role
        # abbreviations like "C.J." can't sneak through this token split,
        # but defensive: drop any leftover tokens that don't look like a
        # surname after lowercasing (period-containing tokens like "c.j.").
        # Also drop stoplisted cross-court justices -- a footer signoff is
        # structurally indistinguishable from a cited SCOTUS lineup, so a
        # famous non-local justice surname here is a citation, not a panelist.
        names = re.split(r",\s*(?:and\s+)?|\s+and\s+", names_blob)
        names = [
            n.strip() for n in names
            if n.strip() and "." not in n
            and _valid_surname(n)
            and n.strip().lower() not in _CROSS_COURT_JUSTICES
        ]
        # Fallback author detection when no explicit chief prefix was
        # found: a single surname tagged C.J. / P.J. or a single-name J.
        # signoff is the author by convention.
        if author_last is None and role in ("C.J.", "P.J.") and names:
            author_last = names[0].lower()
        elif author_last is None and role == "J." and len(names) == 1:
            author_last = names[0].lower()
        all_panel.extend(n.lower() for n in names)

    # --- Dissenters in the same NH footer ---
    # The "concurred; X, J., dissented." continuation lives in the same
    # tail window as the majority signoff. Each match is a DISSENT_AUTHOR
    # vote -- they wrote a separate dissenting opinion. Dedupe across
    # matches (defensive: the same name shouldn't appear twice in a
    # single footer, but be safe).
    # Seed with any AZ/MN-path dissenters collected above so the panel
    # filter below drops them too (they're separate-opinion writers, not
    # majority joiners), then extend with the NH footer matches.
    seen_dissenters: set[str] = set(dissenter_lasts)
    for m in _DISSENT_FOOTER_RE.finditer(tail):
        # Same parenthetical guard: "(Bassett, J., dissented)" in a citation
        # is not this opinion's dissenter.
        if _inside_open_paren(tail, m.start()):
            continue
        ln = (m.group("name") or "").lower()
        if (ln and _valid_surname(ln) and ln not in seen_dissenters
                and ln not in _CROSS_COURT_JUSTICES):
            seen_dissenters.add(ln)
            dissenter_lasts.append(ln)
            raw_matches.append(m.group(0))

    # Dedupe + drop the author from panel (author already counted via PV)
    seen = set()
    panel: list[str] = []
    for n in all_panel:
        if n in seen:
            continue
        if author_last is not None and n == author_last:
            continue
        seen.add(n)
        panel.append(n)
    # Also drop any name appearing both in panel and dissenters/concurrers --
    # they wrote (or joined) a separate opinion, not the majority.
    separate = seen_dissenters | set(concurrer_lasts)
    panel = [n for n in panel if n not in separate]
    # A name in BOTH lists (e.g. "concurred in part and dissented in part"
    # matched by two patterns) counts as a dissenter -- the stronger signal.
    concurrer_lasts = [n for n in concurrer_lasts if n not in seen_dissenters]
    return GenericByline(
        author_last=author_last,
        panel_last=panel,
        dissenter_last=dissenter_lasts,
        concurrer_last=concurrer_lasts,
        raw_matches=raw_matches,
    )


# Title-case for display when we create new Judge rows from a byline-
# only last name -- "MACDONALD" -> "Macdonald" reads better in the
# admin + on dossier pages. Editors can rename later to the canonical
# capitalization (e.g. "MacDonald").
def _titlecase_surname(upper: str) -> str:
    return upper[:1] + upper[1:].lower() if upper else upper


def _last_name(name: str) -> str:
    """Return the last token of ``name`` after stripping role suffix.

    'Jennifer L. Frisch'      -> 'Frisch'
    'Frisch, Judge'           -> 'Frisch'
    'L. Frisch, J.'           -> 'Frisch'
    'Van Buren, Judge'        -> 'Buren'   (acceptable miss for v1)
    """
    if not name:
        return ""
    cleaned = _ROLE_SUFFIX_RE.sub("", name).strip()
    # Re-strip just in case ",..." remains
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0].strip()
    words = cleaned.split()
    if not words:
        return ""
    return words[-1].strip(".,'-")


class Command(BaseCommand):
    help = "Resolve byline + panel names to Judges and auto-create PanelVote rows."

    def add_arguments(self, parser):
        parser.add_argument("--state", default="MN", help="State code (default MN).")
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Process at most N opinions (smoke-test convenience).",
        )
        parser.add_argument(
            "--since", default=None,
            help="Only opinions filed >= YYYY-MM-DD. Useful for incremental re-runs.",
        )
        parser.add_argument(
            "--min-id", type=int, default=0,
            help=(
                "Only opinions with id > this. Stateless resume cursor: the "
                "byline extraction is CPU-heavy and NFSN culls long processes "
                "on CPU time, so a full state is run as bounded chunks -- each "
                "run prints 'next --min-id <N>' to feed the next one."
            ),
        )
        parser.add_argument(
            "--max-runtime", type=int, default=0,
            help=(
                "Self-exit cleanly after N seconds (0 = run to completion). "
                "Keeps a run under NFSN's CPU-time cull; pair with --min-id to "
                "resume. Mirrors embed_opinions."
            ),
        )
        parser.add_argument(
            "--id-batch", type=int, default=0,
            help=(
                "Read at most N candidate ids per run (0 = all remaining). "
                "At a low --min-id the ordered id-list read alone is tens of "
                "seconds; capping it keeps per-chunk setup cheap so a chunked "
                "sweep isn't dominated by re-reading the shrinking id list. A "
                "run that fills the cap reports a resume cursor even if it "
                "didn't hit --max-runtime."
            ),
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Compute matches + counts but don't create PanelVote rows.",
        )
        parser.add_argument(
            "--create-missing", action="store_true",
            help=(
                "Create Judge rows for byline + panel last-names that "
                "don't match an existing roster. Use for states whose "
                "judges weren't seeded by a roster scraper -- byline-"
                "learned Judges get status=UNKNOWN + "
                "is_currently_seated=False so an editor can review + "
                "promote them later. Idempotent across re-runs via the "
                "(state, slug) unique constraint."
            ),
        )

    def handle(self, *args, state, limit, since, dry_run, create_missing,
               min_id, max_runtime, id_batch, **options):
        # Local import: parsing module loads the state-parser registry
        from opinions.parsing import parse as parse_opinion

        state_code = state.upper()

        # Batch command: lift settings' 25s web-tier max_statement_time so the
        # corpus-wide COUNT + scan don't get KILLed under contention (errno
        # 1969). Vendor-guarded so local SQLite dev is a clean no-op. See the
        # "Batch commands MUST lift max_statement_time" gotcha in CLAUDE.md.
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        # Build last_name -> [Judge,...] lookup for the state. Ambiguity
        # (multiple judges sharing a last name) gets logged + skipped.
        # SUFFIX-AWARE surname (judge_merge.surname), not _last_name:
        # _last_name("Albert Tate jr") is "jr", which made every suffixed
        # judge unmatchable by their real surname -- the resolver then
        # minted a bare-surname shadow beside the full row (36 LA pairs
        # found by audit_judges, 2026-08-25). _last_name stays correct
        # for BYLINE text, which carries roles, not generational rows.
        from opinions.judge_merge import surname as _judge_surname
        judges = list(Judge.objects.filter(state__code=state_code))
        last_name_map: dict[str, list[Judge]] = defaultdict(list)
        for j in judges:
            ln = _judge_surname(j.full_name)
            if ln:
                last_name_map[ln.lower()].append(j)

        ambiguous_names = sum(1 for v in last_name_map.values() if len(v) > 1)
        unique_names = sum(1 for v in last_name_map.values() if len(v) == 1)

        self.stdout.write(self.style.SUCCESS(
            f"Resolving panels for {state_code}: "
            f"{len(judges)} judges, "
            f"{unique_names} unique-last-name lookups, "
            f"{ambiguous_names} ambiguous (skipped)"
            + ("  [--create-missing ON]" if create_missing else "")
        ))

        # Cache State row -- needed when --create-missing forges new Judges.
        from opinions.models import State as _State
        state_obj = _State.objects.get(code=state_code)

        # Counter for byline-learned Judges (only meaningful when
        # create_missing is on). Tracks per-name first-create so we can
        # log a single summary at the end.
        forged_judges: int = 0

        # ---- Service windows, for disambiguating shared surnames --------
        #
        # A bare "Anderson, J." byline was previously DISCARDED whenever a
        # state had more than one Anderson, because guessing wrong is worse
        # than recording nothing. Minnesota has four (Barry A., G. Barry,
        # Paul H., Russell A.), three Gallaghers and three Petersons, so
        # that safe default was throwing away exactly the most common
        # names.
        #
        # But two judges sharing a surname almost never sit in the same
        # era, and the opinion carries its own filing date. So we can
        # often tell them apart without guessing: if exactly ONE candidate
        # was on the bench when the opinion issued, that is the judge.
        #
        # The window comes from evidence we already trust:
        #   start = appointment_date, else their earliest known vote
        #   end   = their latest known vote -- or OPEN if still seated
        # Those existing votes were themselves resolved unambiguously (a
        # full-name byline, or CL's bulk panel data), so this is not
        # circular: it is using confident attributions to place the
        # uncertain ones.
        #
        # Deliberately conservative:
        #   - only ONE candidate may match, otherwise we skip as before
        #   - a candidate with no dates at all is not considered a match
        #     (we prefer the judge with positive date evidence over one
        #     with none) but is reported by audit_judges as an orphan
        #   - GRACE_DAYS of slack, since our observed vote span is a
        #     lower bound on real tenure, not the tenure itself
        GRACE_DAYS = 365

        vote_spans = {
            r["judge_id"]: (r["first"], r["last"])
            for r in PanelVote.objects
            .filter(judge__state__code=state_code)
            .values("judge_id")
            .annotate(first=models.Min("opinion__release_date"),
                      last=models.Max("opinion__release_date"))
        }

        def _window(j: Judge):
            """(start, end) for a judge, or None if we can't bound them.

            ``end is None`` means GENUINELY OPEN (still on the bench) --
            never "we don't know". That distinction is load-bearing: an
            earlier version returned the raw last-vote date as the end,
            so a judge with an appointment date and ZERO votes came out
            as "1936 .. open" and greedily matched every opinion for the
            next ninety years. MN has exactly that row (Harry H.
            Peterson, appointed 1936, no votes), and two Gallaghers like
            it. A judge we cannot bound is ineligible to win a
            disambiguation -- we would be choosing them on no evidence.
            """
            first, last = vote_spans.get(j.pk, (None, None))
            start = j.appointment_date or first
            if start is None:
                return None                     # no start at all
            if j.is_currently_seated:
                return (start, None)            # open because still serving
            if last is None:
                return None                     # started, but no end evidence
            return (start, last)

        def _in_window(j: Judge, when) -> bool:
            if when is None:
                return False
            win = _window(j)
            if win is None:
                return False
            start, end = win
            if when < start - timedelta(days=GRACE_DAYS):
                return False
            if end is not None and when > end + timedelta(days=GRACE_DAYS):
                return False
            return True

        def _disambiguate(candidates: list, when) -> Judge | None:
            """Exactly one candidate on the bench at ``when``, else None."""
            hits = [j for j in candidates if _in_window(j, when)]
            return hits[0] if len(hits) == 1 else None

        def _get_or_create_byline_judge(last_lower: str) -> Judge | None:
            """Return Judge for ``last_lower`` (state-scoped), creating one
            when --create-missing is on and no roster row exists.

            Updates ``last_name_map`` in place so subsequent opinions in
            the same run hit the cache instead of re-querying. Skips
            the create path when last_lower is ambiguous against the
            existing roster -- we'd rather miss than mint a duplicate.
            """
            nonlocal forged_judges
            existing = last_name_map.get(last_lower, [])
            if len(existing) == 1:
                return existing[0]
            if len(existing) > 1:
                # Ambiguous against roster -- caller decides what to do
                # (currently: skip + increment the ambiguous counter).
                return None
            if not create_missing:
                return None
            # Forge a Judge from the byline last-name only. Editor can
            # rename + upgrade status later via admin.
            display_name = _titlecase_surname(last_lower.upper())
            base_slug = slugify(display_name) or last_lower
            # (state, slug) is unique_together; suffix with -<n> if needed.
            slug = base_slug
            n = 2
            while Judge.objects.filter(state=state_obj, slug=slug).exists():
                slug = f"{base_slug}-{n}"
                n += 1
            if dry_run:
                # Synthesize a fake row so downstream logic doesn't crash;
                # don't hit the DB.
                new_j = Judge(state=state_obj, full_name=display_name, slug=slug)
            else:
                new_j = Judge.objects.create(
                    state=state_obj,
                    full_name=display_name,
                    slug=slug,
                    status=Judge.Status.UNKNOWN,
                    is_currently_seated=False,
                    source_id=f"byline:{state_code}:{last_lower}",
                )
            last_name_map[last_lower].append(new_j)
            forged_judges += 1
            return new_j

        # Pre-resolve court IDs so the scan is an FK-index lookup, not a JOIN
        # over the 2.75GB opinions table (the documented perf gotcha).
        court_ids = list(
            _State.objects.get(code=state_code).courts.values_list("id", flat=True)
        )
        # Fetch a CHEAP ordered id list first (PK + court_id index only, no
        # raw_text). Pulling raw_text via select_related+order_by in one big
        # query forces a filesort over the full corpus THEN streams 100KB
        # blobs, and the first chunk fetch alone can eat a whole --max-runtime
        # budget before any opinion is processed. The raw_text="" filter is
        # dropped here (it would read the blob) and empties are skipped in the
        # loop instead. order_by("id") + id__gt makes --min-id a stable cursor.
        id_qs = Opinion.objects.filter(court_id__in=court_ids, id__gt=min_id)
        if since:
            try:
                cutoff = date.fromisoformat(since)
            except ValueError:
                self.stderr.write(f"Bad --since date: {since!r}; use YYYY-MM-DD.")
                return
            id_qs = id_qs.filter(release_date__gte=cutoff)
        id_query = id_qs.order_by("id").values_list("id", flat=True)
        if id_batch:
            id_query = id_query[:id_batch]
        id_list = list(id_query)
        # A run that exactly fills the id-batch cap has almost certainly not
        # reached the true end of the corpus -- there are more ids past it.
        batch_capped = bool(id_batch) and len(id_list) == id_batch

        total = len(id_list)
        if limit:
            total = min(total, limit)

        self.stdout.write(
            f"  scanning {total:,} opinions"
            + (f" filed since {since}" if since else "")
            + ("." if not dry_run else " (DRY RUN; no DB writes).")
        )

        scanned = 0
        author_resolved = panel_resolved = 0
        author_ambiguous = panel_ambiguous = 0
        disambiguated = 0
        new_author_votes = new_join_votes = upgraded_votes = 0
        new_dissent_votes = dissent_ambiguous = 0
        new_concur_votes = concur_ambiguous = 0
        last_id = min_id
        timed_out = False
        t0 = time.time()

        # PK-windowed fetch: pull full rows (with raw_text) in small batches
        # keyed on the pre-materialized id list, so no single query streams the
        # whole corpus and the time budget is checked between real work units.
        _BATCH = 200

        def _iter_opinions():
            for bstart in range(0, len(id_list), _BATCH):
                batch_ids = id_list[bstart:bstart + _BATCH]
                rows = (Opinion.objects.filter(id__in=batch_ids)
                        .select_related("court").order_by("id"))
                for op in rows:
                    yield op

        for opinion in _iter_opinions():
            if limit and scanned >= limit:
                break
            if max_runtime and (time.time() - t0) >= max_runtime:
                timed_out = True
                break
            # Advance the cursor on every row seen (incl. skipped empties) so a
            # resumed run never re-scans them; the dropped exclude(raw_text="")
            # is enforced here instead.
            last_id = opinion.id
            if not opinion.raw_text:
                continue
            scanned += 1

            if scanned % 2_000 == 0:
                elapsed = time.time() - t0
                rate = scanned / max(elapsed, 0.001)
                eta = (total - scanned) / max(rate, 0.001)
                self.stdout.write(
                    f"  scanned {scanned:>6,}/{total:,}  "
                    f"author={new_author_votes:>5,}  "
                    f"join={new_join_votes:>5,}  "
                    f"dissent={new_dissent_votes:>4,}  "
                    f"upgraded={upgraded_votes:>4,}  "
                    f"({rate:>4.0f}/s, eta {eta/60:.0f}min)",
                    ending="\n",
                )

            # Hybrid extraction. The state-specific parser is preferred
            # for author + panel when it returns them, but state parsers
            # commonly leave one or both empty (e.g. the NH parser
            # captures the author byline at the top of the document but
            # doesn't try to parse the "<NAME>, C.J., and <NAMES>, JJ.,
            # concurred." footer -- that's _extract_generic_byline's
            # specialty). For each opinion we run the parser, then run
            # the generic extractor and use it as a fallback for any
            # field the parser left empty.
            result = parse_opinion(state_code, opinion.raw_text)
            if result is not None:
                author_last = (
                    _last_name(result.author).lower() if result.author else None
                )
                panel_lasts = [_last_name(p).lower() for p in result.panel]
                panel_lasts = [p for p in panel_lasts if p]
                # Guard the PARSER-provided fields the same way the
                # generic extractor guards its own captures. The hybrid
                # path used to trust the parser unfiltered, which is how
                # the LA panel leaks ("C", "Defendant", "Tempore") and
                # the per-curiam author string ("Curiam") became judges
                # with tens of thousands of votes between them
                # (2026-08-25). "Per Curiam" is a legitimate DISPLAY
                # author on the opinion page; it is never a person.
                if author_last is not None and not _valid_surname(author_last):
                    author_last = None
                panel_lasts = [p for p in panel_lasts if _valid_surname(p)]
            else:
                author_last = None
                panel_lasts = []
            # Always run the generic extractor in addition to the parser:
            # the parser handles author + panel for its state, but only
            # the generic extractor scans the NH-style "; X, J., dissented."
            # continuation that carries dissenter names. Without this the
            # concordance page on judge_compare always read 100% agreement
            # for NH pairs since no DISSENT_AUTHOR votes existed.
            generic = _extract_generic_byline(opinion.raw_text)
            if author_last is None and generic.author_last:
                author_last = generic.author_last
            if not panel_lasts and generic.panel_last:
                panel_lasts = list(generic.panel_last)
            # The generic extractor treats the byline-footer chief justice
            # ("X, C.J., and ...") as the AUTHOR when running standalone --
            # that's right when there's no parser, since the chief is the
            # only signer it has. But when the parser identified a
            # different author at the top of the document (the real
            # signer), the chief is actually a JOINING panel member.
            # Promote them into the panel list.
            if (
                generic.author_last
                and generic.author_last != author_last
                and generic.author_last not in panel_lasts
                and generic.author_last not in generic.dissenter_last
            ):
                panel_lasts.append(generic.author_last)
            # Remove dissenters from the panel-joiner list if they accidentally
            # appeared there (defensive -- the generic extractor already
            # excludes dissenters from its panel output, but a state parser
            # may not have).
            dissenter_lasts = list(generic.dissenter_last)
            concurrer_lasts = list(generic.concurrer_last)
            if dissenter_lasts or concurrer_lasts:
                separate_set = set(dissenter_lasts) | set(concurrer_lasts)
                panel_lasts = [p for p in panel_lasts if p not in separate_set]

            # ---- Pass 1: Author ----
            author_judge: Judge | None = None
            if author_last:
                pre_existing = last_name_map.get(author_last, [])
                if len(pre_existing) > 1:
                    author_judge = _disambiguate(pre_existing, opinion.release_date)
                    if author_judge is not None:
                        author_resolved += 1
                        disambiguated += 1
                    else:
                        author_ambiguous += 1
                else:
                    author_judge = _get_or_create_byline_judge(author_last)
                    if author_judge is not None:
                        author_resolved += 1

            if author_judge and not dry_run:
                pv, created = PanelVote.objects.get_or_create(
                    opinion=opinion,
                    judge=author_judge,
                    defaults={"vote_type": PanelVote.Vote.MAJORITY_AUTHOR},
                )
                if created:
                    new_author_votes += 1
                elif pv.vote_type == PanelVote.Vote.MAJORITY_JOIN:
                    # Existing CL-loaded row only knew "joined majority";
                    # parser confirms this judge actually authored.
                    pv.vote_type = PanelVote.Vote.MAJORITY_AUTHOR
                    pv.save(update_fields=["vote_type"])
                    upgraded_votes += 1

            # ---- Pass 2: Panel members ----
            for panel_last in panel_lasts:
                pre_existing = last_name_map.get(panel_last, [])
                if len(pre_existing) > 1:
                    _picked = _disambiguate(pre_existing, opinion.release_date)
                    if _picked is None:
                        panel_ambiguous += 1
                        continue
                    disambiguated += 1
                    _forced = _picked
                else:
                    _forced = None

                panel_judge = _forced or _get_or_create_byline_judge(panel_last)
                if panel_judge is None:
                    continue
                if author_judge is not None and panel_judge.pk == author_judge.pk:
                    # Already counted as author; don't downgrade to "joined".
                    continue
                panel_resolved += 1

                if not dry_run:
                    _, created = PanelVote.objects.get_or_create(
                        opinion=opinion,
                        judge=panel_judge,
                        defaults={"vote_type": PanelVote.Vote.MAJORITY_JOIN},
                    )
                    if created:
                        new_join_votes += 1

            # ---- Pass 3: Dissenters ----
            # Each dissenter wrote a separate dissenting opinion, so
            # the vote type is DISSENT_AUTHOR. If a prior pass wrote a
            # MAJORITY_JOIN row for this same judge (e.g. a CL bulk
            # load that didn't distinguish dissents), upgrade it in
            # place rather than leaving conflicting data.
            for dissenter_last in dissenter_lasts:
                pre_existing = last_name_map.get(dissenter_last, [])
                if len(pre_existing) > 1:
                    _picked = _disambiguate(pre_existing, opinion.release_date)
                    if _picked is None:
                        dissent_ambiguous += 1
                        continue
                    disambiguated += 1
                    _forced = _picked
                else:
                    _forced = None

                dissent_judge = _forced or _get_or_create_byline_judge(dissenter_last)
                if dissent_judge is None:
                    continue

                if not dry_run:
                    pv, created = PanelVote.objects.get_or_create(
                        opinion=opinion,
                        judge=dissent_judge,
                        defaults={"vote_type": PanelVote.Vote.DISSENT_AUTHOR},
                    )
                    if created:
                        new_dissent_votes += 1
                    elif pv.vote_type == PanelVote.Vote.MAJORITY_JOIN:
                        # CL bulk row mis-classified -- the footer says
                        # this judge dissented, not joined.
                        pv.vote_type = PanelVote.Vote.DISSENT_AUTHOR
                        pv.save(update_fields=["vote_type"])
                        upgraded_votes += 1

            # ---- Pass 4: Concurrers (special / separate concurrences) ----
            # Same shape as Pass 3. A "Concurring, X, Judge" caption (MN)
            # or "Justice X specially concurred" (AZ) is a separate
            # concurring opinion -- CONCURRENCE_AUTHOR. A bulk-loaded
            # MAJORITY_JOIN row for the same judge gets upgraded: they
            # agreed with the outcome but wrote separately, and the
            # concordance/heat features bucket that as "partial".
            for concurrer_last in concurrer_lasts:
                pre_existing = last_name_map.get(concurrer_last, [])
                if len(pre_existing) > 1:
                    _picked = _disambiguate(pre_existing, opinion.release_date)
                    if _picked is None:
                        concur_ambiguous += 1
                        continue
                    disambiguated += 1
                    _forced = _picked
                else:
                    _forced = None

                concur_judge = _forced or _get_or_create_byline_judge(concurrer_last)
                if concur_judge is None:
                    continue
                if author_judge is not None and concur_judge.pk == author_judge.pk:
                    continue

                if not dry_run:
                    pv, created = PanelVote.objects.get_or_create(
                        opinion=opinion,
                        judge=concur_judge,
                        defaults={"vote_type": PanelVote.Vote.CONCURRENCE_AUTHOR},
                    )
                    if created:
                        new_concur_votes += 1
                    elif pv.vote_type == PanelVote.Vote.MAJORITY_JOIN:
                        pv.vote_type = PanelVote.Vote.CONCURRENCE_AUTHOR
                        pv.save(update_fields=["vote_type"])
                        upgraded_votes += 1

        elapsed = time.time() - t0
        # More work remains if we stopped on the clock or only saw a capped
        # slice of ids. Either way we hand back a cursor; only a run that
        # drained an uncapped (or under-cap) id list has reached the true end.
        more_remaining = timed_out or batch_capped
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{'Stopped' if more_remaining else 'Done'} in {elapsed/60:.1f} min."
        ))
        if more_remaining:
            reason = "time budget hit" if timed_out else "id-batch cap filled"
            self.stdout.write(self.style.WARNING(
                f"  {reason}; resume the rest with:  --min-id {last_id}"
            ))
        else:
            self.stdout.write(f"  reached end of corpus (last id {last_id}).")
        self.stdout.write(
            f"  scanned:             {scanned:>7,}\n"
            f"  authors resolved:    {author_resolved:>7,}  "
            f"(ambiguous skipped: {author_ambiguous})\n"
            f"  panels resolved:     {panel_resolved:>7,}  "
            f"(ambiguous skipped: {panel_ambiguous})\n"
            f"  new author votes:    {new_author_votes:>7,}\n"
            f"  new joined votes:    {new_join_votes:>7,}\n"
            f"  new dissent votes:   {new_dissent_votes:>7,}  "
            f"(ambiguous skipped: {dissent_ambiguous})\n"
            f"  new concur votes:    {new_concur_votes:>7,}  "
            f"(ambiguous skipped: {concur_ambiguous})\n"
            f"  upgraded (J->A/D/C): {upgraded_votes:>7,}\n"
            f"  disambiguated by date:{disambiguated:>6,}  "
            f"(shared surname resolved via service window)"
            + (
                f"\n  byline-learned judges (status=UNKNOWN): {forged_judges:>7,}"
                if create_missing else ""
            )
            + ("\n  (DRY RUN -- nothing saved)" if dry_run else "")
        )
