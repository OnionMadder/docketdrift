"""Extractive holding finder -- the court's own holding sentence, verbatim.

This is the deterministic alternative to LLM holding summarization. It does
not generate, paraphrase, or compress: it locates the sentence in which the
court states what it decided, and returns that sentence exactly as written,
plus the court's own paragraph number for a pinpoint deep link.

Why extraction rather than an LLM summary
-----------------------------------------
A corpus scan (3,001 NH opinions) found that appellate courts announce their
holdings with a small, stable set of signal phrases:

    we conclude          80.9%       we determine          10.2%
    accordingly, we      47.6%       we cannot say          5.9%
    we agree             34.3%       we find that           3.5%
    we disagree          32.1%       we therefore conclude  2.3%
    we decline to        15.4%       the issue before us    2.3%
    we hold              14.3%       we now hold            0.5%

84% of NH opinions (69% MN, 24% AZ) state a holding this way. For those, an
LLM summary would be a lossy, unverifiable paraphrase of a sentence we can
quote exactly -- and quoting exactly is the whole product posture. See
``/how-we-differ/``: we do not synthesize legal text.

What we deliberately do NOT treat as a holding
----------------------------------------------
Frequency alone is a trap. Three of the most common phrases are not holdings:

- ``accordingly, we ...`` (47.6%) is nearly always the DISPOSITION sentence
  ("Accordingly, we affirm.") -- that is ``Opinion.disposition``'s job, and
  it carries no legal proposition.
- ``we agree`` / ``we disagree`` (34% / 32%) usually characterize a party's
  argument mid-analysis ("We disagree with the defendant's reading of...").
- ``we find that`` reports a factual finding, not a legal holding.

Including them would triple raw coverage and wreck precision, which is the
opposite of the tradeoff we want.

Tiering
-------
An explicit "we hold" outranks "we conclude": when a court says *hold* it is
announcing a rule, while *conclude* is also used for interim analytical steps.
So if a tier-1 signal appears anywhere, only tier-1 matches are returned --
we never dilute an explicit holding with weaker sentences.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Tier 1 -- the court is announcing a rule. Strongest possible signal.
_TIER1 = (
    r"we\s+(?:therefore\s+|thus\s+|accordingly\s+|now\s+|also\s+|further\s+)?hold",
    r"we\s+so\s+hold",
)

# Tier 2 -- the workhorse. Reliable, but also used for interim steps, so it
# is only consulted when the opinion contains no explicit "hold".
_TIER2 = (
    r"we\s+(?:therefore\s+|thus\s+|accordingly\s+|further\s+|also\s+)?conclude",
    r"we\s+are\s+persuaded\s+that",
)

_TIER1_RE = re.compile(r"\b(?:" + "|".join(_TIER1) + r")\b", re.IGNORECASE)
_TIER2_RE = re.compile(r"\b(?:" + "|".join(_TIER2) + r")\b", re.IGNORECASE)

# Court-assigned paragraph marker: "[¶12]" or "¶12". We resolve the marker
# that PRECEDES the holding sentence. Only the court's own numbering is ever
# used -- we never synthesize a paragraph number, because a fabricated
# pinpoint cite misstates the record.
_PARA_RE = re.compile(r"\[?¶\s*(\d{1,4})\]?")

# Tokens that end in "." but do not end a sentence. Without these, legal
# prose shatters: "RSA 91-A:4, III." and "State v. Smith, 141 N.H. 271." and
# "Inc." would each split mid-citation.
_ABBREV = {
    "n.h", "u.s", "s.ct", "f.2d", "f.3d", "a.2d", "a.3d", "p.2d", "p.3d",
    "n.w", "n.w.2d", "so.2d", "v", "vs", "inc", "co", "corp", "ltd", "llc",
    "jr", "sr", "mr", "mrs", "ms", "dr", "st", "no", "nos", "art", "amend",
    "stat", "rev", "ann", "ch", "sec", "subd", "para", "pp", "ed", "eds",
    "cf", "e.g", "i.e", "id", "supra", "cert", "rsa", "ariz", "minn", "app",
    "ct", "dist", "j", "jj", "c.j", "et al", "al", "op", "cit", "seq",
}

# Cheap guard: a "holding" that is mostly citation or is a fragment is not
# quotable, and a runaway match usually means sentence detection failed.
_MIN_HOLDING_CHARS = 40
_MAX_HOLDING_CHARS = 700


@dataclass(frozen=True)
class ExtractedHolding:
    """One holding sentence, exactly as the court wrote it."""

    text: str
    paragraph: int | None
    signal: str          # "hold" or "conclude" -- which tier matched
    offset: int          # char offset in raw_text, for auditing


def _is_abbreviation(text: str, period_index: int) -> bool:
    """True when the "." at ``period_index`` closes a known abbreviation."""
    # Walk back over the word (and any interior dots, so "N.H." is one token).
    start = period_index
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "."):
        start -= 1
    token = text[start:period_index].lower().strip(".")
    if token in _ABBREV:
        return True
    # A single initial ("J. Smith") or a lone letter is never a sentence end.
    return len(token) == 1


def _sentence_bounds(text: str, match_start: int) -> tuple[int, int]:
    """Return (start, end) of the sentence containing ``match_start``.

    Scans outward from the match rather than splitting the whole document --
    cheaper, and it keeps the abbreviation logic local to the one sentence we
    actually care about.
    """
    # --- backward to the sentence start -------------------------------
    start = 0
    i = match_start - 1
    while i > 0:
        ch = text[i]
        if ch in ".?!" and not (ch == "." and _is_abbreviation(text, i)):
            start = i + 1
            break
        # A paragraph marker also opens a sentence.
        if ch == "¶":
            m = _PARA_RE.match(text, max(0, i - 1)) or _PARA_RE.match(text, i)
            if m:
                start = m.end()
                break
        i -= 1

    # --- forward to the sentence end ----------------------------------
    end = len(text)
    j = match_start
    while j < len(text):
        ch = text[j]
        if ch in ".?!" and not (ch == "." and _is_abbreviation(text, j)):
            end = j + 1
            break
        j += 1

    return start, end


def _paragraph_before(text: str, offset: int) -> int | None:
    """Court-assigned paragraph number in effect at ``offset``, if any.

    Returns the number from the nearest preceding marker. Never invents one:
    opinions without court numbering (most MN, all historic NH) return None,
    and the caller must not fall back to a positional count.
    """
    last = None
    for m in _PARA_RE.finditer(text, 0, offset + 1):
        last = m
    return int(last.group(1)) if last else None


def extract_holdings(
    raw_text: str, max_holdings: int = 3
) -> list[ExtractedHolding]:
    """Return up to ``max_holdings`` holding sentences, verbatim.

    Tier 1 ("we hold") wins outright when present -- an explicit holding is
    never diluted with weaker "we conclude" sentences from the same opinion.
    """
    if not raw_text:
        return []

    # Normalize whitespace but keep offsets meaningful by working on the
    # normalized copy throughout (paragraph markers survive the collapse).
    text = " ".join(raw_text.split())

    for signal, pattern in (("hold", _TIER1_RE), ("conclude", _TIER2_RE)):
        found: list[ExtractedHolding] = []
        seen: set[str] = set()
        for m in pattern.finditer(text):
            start, end = _sentence_bounds(text, m.start())
            sentence = text[start:end].strip()
            if not (_MIN_HOLDING_CHARS <= len(sentence) <= _MAX_HOLDING_CHARS):
                continue
            key = sentence.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(ExtractedHolding(
                text=sentence,
                paragraph=_paragraph_before(text, start),
                signal=signal,
                offset=start,
            ))
            if len(found) >= max_holdings:
                break
        if found:
            return found

    return []


def summarize_holdings(
    raw_text: str, max_holdings: int = 3
) -> tuple[str, list[int]]:
    """Adapter for ``Opinion.holding_summary`` / ``holding_source_paras``.

    Returns ``("", [])`` when the court never states a holding in a form we
    recognize -- blank is honest; a guess is not.
    """
    holdings = extract_holdings(raw_text, max_holdings=max_holdings)
    if not holdings:
        return "", []
    summary = " ".join(h.text for h in holdings)
    paragraphs = [h.paragraph for h in holdings if h.paragraph is not None]
    return summary, paragraphs
