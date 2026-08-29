---
name: case-law-research
description: Research U.S. state appellate case law using the DocketDrift corpus (Minnesota, New Hampshire, Arizona, Louisiana). Use when the user asks about state court opinions, needs to verify or look up a citation, wants to know whether a case is still good law, is researching a judge's record, or asks what courts have said about a statute. Enforces verbatim quotation and honest coverage limits.
---

# Researching state case law with DocketDrift

DocketDrift serves **verbatim** opinion text from official sources. It
generates nothing — no summaries, no paraphrases, no synthesized holdings.
That property is the entire point: a citation it returns either resolves to a
real opinion or does not resolve at all. Preserve that property in how you
use it.

## Coverage — know the edges before you rely on it

Roughly 470,000 opinions across four states:

| State | Span | Courts |
|---|---|---|
| Minnesota | 1851–present | Supreme Court, Court of Appeals |
| New Hampshire | 1816–present | Supreme Court (no intermediate appellate court) |
| Arizona | 1866–present | Supreme Court, Court of Appeals Div. One and Div. Two |
| Louisiana | 1809–present | Supreme Court, all five Circuit Courts of Appeal |

**There is nothing outside these four states.** No federal courts, no other
state, no statutes-as-text, no briefs or dockets. If a question needs
authority DocketDrift does not hold, say so plainly and stop — do not
substitute recalled case law for a lookup. An unverified citation is the
specific failure this corpus exists to prevent.

Known gaps that change what an absence means:

- **Louisiana Supreme Court, 2020–2025** is roughly 3% present. The upstream
  source has the hole; the opinions exist. A missing recent LA Supreme case
  is far more likely a coverage gap than a nonexistent case.
- **Louisiana has no reporter citations at all.** `lookup_citation` on a
  So. 2d / So. 3d cite will not resolve. Search by docket number or text.
- **Minnesota 2017–2025** was rebuilt from the state law library archive and
  carries no reporter citations, and no Supreme Court *orders* (attorney
  discipline, administrative dockets).
- **Louisiana Court of Appeal circuit labels are approximate** on older,
  scan-quality writ rulings. Trust the circuit on signed opinions; treat it
  as indicative on brief writ dispositions.

When coverage is the reason you cannot answer, say which gap applies. "I
don't find it" and "our source is missing that period" are different claims
and a researcher needs the difference.

## Tools, and when each is right

- **`search_opinions`** — keyword search within one state. Requires `state`.
  Narrow queries work well; a broad common-law term ("negligence") scans the
  whole corpus, is slow, and returns a *sample rather than a ranking* — the
  response says so when it happens. Add distinguishing terms instead of
  re-running the broad query.
- **`lookup_citation`** — resolve a reporter cite or docket number directly.
  Use this first whenever the user gives you a citation; it is far cheaper
  and more certain than searching for the case name.
- **`get_opinion`** — full text and metadata for one opinion. Full text is
  opt-in and capped; request it only when you need to read the reasoning.
- **`citing_opinions`** — what later cases said about this one, with the
  verbatim citing passage and a treatment classification.
- **`get_judge`** — a judge's record: panel votes, disposition lean,
  co-panelist alignment.
- **`get_statute`** — opinions construing a given statute.

All six are read-only. None of them writes, and none reaches a third-party
service.

## Rules that matter

**Quote, never paraphrase, when it counts.** Holdings come back as the
court's own sentence. Present them as a quotation attributed to the court.
If you compress for readability, make clear which words are the court's and
which are yours. Never present a paraphrase as the holding.

**Never invent a pinpoint.** Some opinions carry court-assigned paragraph
numbers and support `#para-N` deep links; many do not. Cite `¶N` only when
the tool returned that paragraph number. Constructing one by counting
paragraphs yourself misstates the record — it is the single worst error
available here, because it looks precisely like the thing it is faking.

**Check treatment before relying on a case.** "Is this still good law" is the
question a practitioner most needs answered. Run `citing_opinions` before
presenting a case as authority, and report a negative treatment
(overruled, criticized) prominently rather than in a footnote. Treatment
classification is regex-derived from the citing court's own language, so
quote the citing passage and let the reader judge — do not assert that a
case *is* overruled on the strength of a label alone.

**A disposition is transcribed, not interpreted.** Historic opinions close
with terms like "Exceptions overruled" or "Case discharged." These are
recorded exactly as written and deliberately not mapped onto modern
affirmed/reversed categories. Report the court's actual words; do not
translate them.

**Cite so the reader can verify.** Give docket number, court, and date, and
include the canonical URL each tool returns. That URL is the verification
path — it is what separates a real citation from a plausible one.

## Honest failure

If search returns nothing, say so and say what you searched. If the endpoint
reports that search is busy, retry once or narrow the query — do not fall
back to answering from memory. The value of this corpus is that its answers
are checkable, and an unverifiable answer delivered confidently is worth
less than no answer at all.
