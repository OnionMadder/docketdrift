"""New Hampshire case-citation extractor for the citation graph.

NH adopted neutral citations effective January 1, 2024: opinions cite each
other as ``<Case Name>, <year> N.H. <n>`` (e.g. "State v. Romero, 2026 N.H.
7"). We extract the neutral cite plus a surrounding context window. The cite
normalizes to the exact ``<year> N.H. <n>`` form stored in
``Opinion.reporter_cite``, so internal edges resolve by exact match.

Out of scope for v1 (don't resolve to our corpus, add false-positive noise):
  - pre-2024 NH regional cites ("123 N.H. 456")
  - regional/federal reporters ("900 A.2d 12", "410 U.S. 113", "88 F.3d 1")
These can be added once we backfill the corresponding reporter cites.
"""
import re

from .citations import ExtractedCitation

# Neutral cite: "<year> N.H. <n>", year 2024+ (the neutral-citation era).
# A 4-digit year >= 2024 immediately before "N.H." is unambiguously a neutral
# reporter cite, not prose, so false positives are negligible.
NEUTRAL_CITE = re.compile(r"\b(?P<year>20\d{2})\s+N\.H\.\s+(?P<num>\d{1,4})\b")

# Context window (chars each side of the cite) kept for treatment cues + UI.
CONTEXT_PAD = 180


def extract(text: str, self_cite: str = "") -> list[ExtractedCitation]:
    if not text:
        return []
    results: list[ExtractedCitation] = []
    seen: set[str] = set()  # one edge per cited cite per opinion
    for m in NEUTRAL_CITE.finditer(text):
        year = int(m.group("year"))
        if year < 2024:  # neutral citations didn't exist before 2024
            continue
        cite = "%s N.H. %s" % (m.group("year"), m.group("num"))
        if cite == self_cite or cite in seen:
            continue
        seen.add(cite)
        start = m.start()
        context = " ".join(
            text[max(0, start - CONTEXT_PAD):m.end() + CONTEXT_PAD].split()
        )
        results.append(ExtractedCitation(
            reporter_cite=cite,
            text_offset=start,
            context=context,
        ))
    results.sort(key=lambda c: c.text_offset)
    return results
