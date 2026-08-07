# DocketDrift — working backlog

Snapshot 2026-08-06. Prioritized. Each item says what it is, why it matters,
and roughly how big. "Onion" items need the member panel or are editorial.

**SEARCH IS FIXED (2026-08-05b).** The concurrency cliff was dead cosine scans
(MN/AZ needed 16-29s vs the 12s bound; every search burned 12 thread-seconds
for nothing). Slim embedding table + 10-yr windowed scans: MN search 13.3s →
1.7s live; 3 concurrent searches 1.3/1.3/2.3s (was 183s for two). Also closed:
QueryEmbedding stored every search query verbatim, contradicting the privacy
promise — dropped, replaced with a process-local cache. Detail in CLAUDE.md.

**What changed 2026-08-06** (detail in CLAUDE.md's session block): MN weekly
scraper rebuilt in manifest mode + success BEACONS wired into freshness_check
(a dead weekly now alerts in ~1 week, not 45 days; weekly forward-fills MN
Supreme too); `/report-error/` live + verified end-to-end (emailed, stored
nowhere; nav link on every page); every-state CL coverage audit committed
(`docs/cl_coverage_audit/` — 17 courts MN-shaped, LA Supreme the worst hole
in the country); Ko-fi "Launch Louisiana" $250 goal live, support page
matches; FLP replied — bridged to #1115, bundle staged at
`/home/private/handover/`, **WAITING, no nudge before ~2 weeks**; onioncore
design sweep — 30 rules, one accent grammar site-wide.

Status baseline (pulled live 2026-08-06, post-AZ-sweep): **128,150 opinions**
(MN 69,292 / AZ 38,135 / NH 20,723). Citation graph **1,462,119 edges** —
605,353 from CourtListener's bulk map plus **856,766 text-extracted**
(MN 468,595 / AZ 313,120 / NH 75,051), of which 638,791 carry a context
quote and 5,188 a non-default treatment (was 0 in every state a week ago).
ALL THREE STATES now have the full citation story: bulk breadth + extracted
quotes/treatment. Parallel cites 180,652. Zero duplicated extracted pairs.

**MN 2017–2025 IS DONE.** Continuous coverage 2015–2025, every year ~970–1,435:
2015 1,435 · 2016 1,415 · 2017 1,350 · 2018 1,327 · 2019 1,431 · 2020 1,040 ·
2021 1,092 · 2022 971 · 2023 1,014 · 2024 1,161 · 2025 1,133; 2026 forward-
filled weekly. ~9,800 opinions rebuilt from the State Law Library archive.
(The 2026-08-05 finish: 2019 H2 + 2023 + 2024–2025 swept clean, 0 CAPTCHAs on
the final runs; two interleaved citation sweeps briefly DOUBLED 345,900
extracted pairs — deduped and verified to 0; a court typo had dated one
opinion 2101, fixed — future dates silently defeat check_freshness.)

**What changed on 2026-08-04** (full detail in CLAUDE.md):
- **MN 2017–2022 backfilled: ~5,900 opinions.** 2020/21/22 went 0 → ~1,000
  each; 2017 438 → 1,350; 2018 208 → 1,327; 2019 176 → 856.
- **41,318 case numbers normalized** — filename stems now 0 remaining, which
  is what made `ingest_pdfs` dedup correctly instead of duplicating years.
- **MN + AZ citation extractors built; NH widened.** NH went 71 → 75,051 edges.
- **ParallelCite loaded** — the single data gap that had been capping the
  citation graph in all three states.
- **eyecite settled with numbers**: installs fine on FreeBSD, measurably not
  worth deploying for graph coverage. See CLAUDE.md before reopening.

---

## ★ Discoverability — MN/AZ AI-grounding fix (SHIPPED 2026-07-25, now monitoring)

**Root cause of the MN=0% AI-grounding puzzle: MN wasn't in the search index.**
Live AI agents ground by web-search-then-fetch, so they can only reach pages
Google/Bing have indexed. Access-log cross-tab (opinion fetches):

| | MN | NH | AZ |
|---|---|---|---|
| Live AI | **0** | 102 | 0 |
| AI training crawlers | 56,674 | 5,787 | 13,795 |
| **Search engines** | **1** | **1,421** | 65 |
| Humans | 24,307 | 430 | 4,630 |

MN is crawled the most by training bots + humans, but Google had crawled it
**once**. The site is technically perfect (200s to Googlebot, robots allows,
sitemap advertised + well-formed) — a **discovery gap, not a bug**. NB: it
turned out **NONE** of the docketdrift domains were in Search Console — NH's
1,421 crawls were purely ORGANIC discovery; MN/AZ just never got that luck.

**Both halves of the fix are now DONE:**

- [x] **Server side — AZ sitemap was uncrawlable. FIXED.** ~20K AZ COA docket
  numbers carry spaces ("1 CA-CV 25-0606 PB"); the sitemap emitted them raw
  (invalid URLs). Now percent-encoded; canonical + og:url match; robots.txt
  advertises only the host's own sitemap. Verified: every sitemap URL (incl.
  the %20-encoded COA ones) resolves 200.
- [x] **Owner side — Search Console. DONE 2026-07-25.** Added `docketdrift.com`
  as a **Domain property** (one DNS TXT record at NFSN covers apex + all
  subdomains + future states), verified, and submitted the mn/nh/az sitemaps.

**First-crawl checkpoint (2026-07-27, ~2 days after submission) — SUBMISSION WORKED:**

Googlebot went 1 → **10,659 hits**. It fetched every sitemap incl. MN's deep
`sitemap-opinions-3.xml` (MN is the only state with a chunk 3), so MN's URLs
ARE discovered. Search Console Sitemaps confirms: **mn = Success, 44,383 pages
discovered; nh = Success, 112**. Googlebot opinion-page hits by state (resolved
via DB, not just format): **NH 7,654 / AZ 256 / MN 8.**

- NH dominates (Google already had organic authority there).
- **AZ is now crawled** (256, was ~65 total) — the %20 sitemap fix paid off.
- **MN = "Discovered, crawl-queued," not missed** — Google has the 44,383 URLs
  but hasn't allocated crawl budget to the cold 60K subdomain yet. Normal
  cold-start ramp; expect MN hits to climb over days–weeks.

**AZ sitemap showed "Couldn't fetch" in Search Console — STALE, not a real
bug.** External HTTPS fetch of `az.docketdrift.com/sitemap.xml` returns 200 +
valid sitemapindex; AZ opinion pages crawl fine (cert/DNS/content all good).
The tell: az's "Last read" was blank (Google's one fetch attempt on submission
day failed and it hadn't retried). **Cause: submitting during a heavy deploy
day** — 6–8 gunicorn restarts on 2026-07-25, and NFSN's front proxy serves a
stale 503 for a few minutes after each `signal-daemon gunicorn TERM`. Google's
az fetch landed in one of those windows; mn/nh got read at good moments. Fix:
re-submit the az sitemap (remove + re-add) to force a fresh fetch.

  **LESSON: never submit / re-submit sitemaps (or ping Google) right after a
  deploy.** Give gunicorn + the NFSN proxy several minutes to settle first, or
  the fetch can hit a stale-503 window and log a false "Couldn't fetch."

**Still monitoring (nothing to build — this is a wait):**

- [ ] Owner: **re-submit the az sitemap** in Search Console (it fetches clean
  now) so it flips to Success + ~38K discovered.
- [ ] **~1 week:** re-check the access log — MN Googlebot hits should climb
  from 8 as crawl budget ramps. Baseline: NH 7,654 / AZ 256 / MN 8.
- [ ] **~weeks:** re-run `ai_citation_profile` and compare to the baseline
  below. MN/AZ climbing = the whole thread (reporter cites + sitemap fix +
  Search Console) paid off.

  **BASELINE — `ai_citation_profile --days 30` on 2026-07-27** (2 days after
  submission, so essentially pre-fix; MN not yet indexed):
  - 123 live-AI fetches (chatgpt-user / claude-user / claude-web /
    perplexity-user), all 123 resolved to a real opinion (0 unmatched — URL
    scheme is clean).
  - By state: **NH 118 (96%) / AZ 5 (4%) / MN 0 (0%)**. MN still zero because
    it's discovered-but-crawl-queued, not indexed — can't surface here yet.
  - Substantive finding worth keeping: **AI reaches for foundational/historic
    law, not just recent** — 17% of fetches were 1930s NH, and the single
    most-fetched opinion is *Nashua Hospital Ass'n v. Gage* (1932) at 12×.
    Argument that the pre-1980 corpus + its historic-tier dispositions are
    disproportionately what AI grounds on, not a completeness nicety.
  - Disposition mix of what AI fetched: affirmed 41% / other 24% / mixed 16%.
    66% precedential.
- [ ] Optional: same submission in **Bing Webmaster Tools** for Bing/Copilot
  coverage.

---

## Tier 0 — clean up the working tree ✅ DONE 2026-07-24

The working tree is now clean and main is in sync with origin. What happened:

- [x] **Landmine neutralized.** Reverted `about.html`, `how_we_differ.html`,
  `apex.html` to HEAD. The parked rewrites asserted an LLM "summarized
  holdings" surface (naming Claude Haiku) in indexed schema.org JSON-LD —
  false for *extractive* holdings. The original "we do not generate text"
  posture is accurate and stronger, so revert was the fix, not a rewrite.
- [x] **Both parked features moved to a branch:**
  `parked/holdings-admin-and-citation-clustering` (pushed). Two commits:
  citation clustering ("How this document has been cited") and the LLM
  holdings admin + `extract_holdings` command. Recoverable per-file via
  cherry-pick. Nothing was deleted.
- [x] **Stale file deleted:** `session-brief.md`.
- [x] **Holdings panel link checked — NOT broken.** It targets the
  `how_we_differ` page generically (no fragment), by design. See below.

### Follow-ups this surfaced (not Tier 0)

- [x] **Citation clustering — SHIPPED NH-first 2026-07-25** ("How this document
  has been cited", verbatim citing passages, Scholar-style collapse). Verified
  rendering on State v. Rouleau. **Caveat / real bottleneck:** it has data on
  only ~41 NH opinions, because the OpinionCitation graph resolves only the
  neutral-cite era (2024+) — older cross-cites use "141 N.H. 271" / A.2d
  formats that don't match a reporter_cite. So the panel is correct but rare.
  The high-value follow-up isn't the panel, it's **improving citation
  resolution** (match N.H.-Reports + A.2d cite formats), which would light up
  BOTH this panel and the already-live cited-by graph.
- [~] **Holdings review admin — salvaged + de-LLM'd, then REVERTED 2026-07-25.**
  Brought the review surface onto main (dropped the LLM `extract_holdings`
  command; reframed all copy from "summarized/Haiku" to verbatim extraction).
  But validation caught two latent bugs (the parked code had never run): a bad
  `.only("court__state__id")` (State's PK is `code`) and — the blocker — the
  `exclude(holding_summary="")` + `order_by(-holding_extracted_at)` queries
  scan the whole 119K corpus (39K holdings, no supporting index) past the 25s
  cap, risking a pooled-connection poison. **To ship it needs an index
  migration on the 2.75GB opinions_opinion table** (e.g. index
  `holding_extracted_at`, filter on `isnull=False`) — a deliberate big-table
  migration, so it's reverted to safe for now. The de-LLM'd salvage is
  recoverable from the reverted commits (7498db0 + 324f9c5). Low urgency:
  verbatim holdings barely need review (no hallucination to catch).
- **`extract_holdings` LLM command: dropped** — not on main (lives only on the
  parked branch as history). The product stays extractive, ML in two places.
- [ ] **Give `/how-we-differ/` an extractive-holdings section**, then repoint
  the holdings panel link at it. Content task — the link is correctly generic
  until then, so this is polish, not a fix.

## Tier 1 — editorial polish (the honest asterisks on the dashboard)

- [~] **AZ judge roster cleanup — PREP PASS DONE 2026-07-25; ~30 admin rows
  left for Onion.** `resolve_judges --create-missing` minted 280 UNKNOWN AZ
  judges from bylines. It was NOT "forged garbage" — 162 were real judges named
  by surname only ("Mcmurdie"), 125 had full names, 38 were zero-vote roster
  orphans, plus a class of PDF hyphenation dupes ("Struck-meyer"). The
  automated prep (data-only, no code; all high-confidence, status left UNKNOWN
  so the human confirm still counts):
  - **30 names completed** to full form — each verified against a byline in an
    opinion that judge AUTHORED, or a single unambiguous zero-vote-roster
    surname match. Covers all the heavily-voted judges (McMurdie 1229, Williams
    711, Lopez 280, Gordon 136, ...). Technique: scan a judge's own opinions'
    bylines for "Judge <First ... Surname> delivered/authored"; also match
    surname-only rows to the roster's full-name rows (suffix-stripped for
    "... Jr").
  - **6 orphan roster rows deleted** (zero-vote dupes consumed by a completion);
    **8 hyphenation artifacts fixed** (Struck-meyer merged into Struckmeyer with
    votes reassigned; 7 de-hyphenated in place). 287 → 280 rows.
  - **LEFT for the admin sit-down** (`/admin/opinions/judge/`, AZ + UNKNOWN):
    confirm the 30 completed (names are right, just flip to reviewed); complete
    the ~3-4 mid-vote surname-only still missing a name (Struckmeyer 168, Prade
    126, Staring 74) + a long low-vote/historical tail; cull the ~32 zero-vote
    orphans.
- [~] **CL crack RAN 2026-07-27 (CL recovered) — and found a bigger bug.** A
  bounded dry-run (17 highest-vote surname-only judges, CL `/people/?name_last=`)
  completed 2 more real AZ Justices (`Struckmeyer` → **Fred C. Struckmeyer
  Jr.**, `Holohan` → **William A. Holohan**) — but the low-vote tail is
  **citation false-positives, not incomplete names**: CL matched `Scalia` →
  *Antonin Scalia*, and the ambiguous hits were `Brennan / Kennedy / Harlan /
  White / Thomas` — all **US Supreme Court** justices. `resolve_judges` minted
  them as AZ "judges" with panel votes because it grabbed cited-justice
  surnames out of the opinion TEXT, not just the panel byline. So the tail
  cleanup is **CULL, not complete**.
- [x] **Phantom-judge CULL DONE 2026-07-27.** Removed **29 citation
  false-positive judges** + their bogus panel votes — every one a SCOTUS
  justice surname (Scalia, Brennan, Rehnquist, Ginsburg, Sotomayor, Gorsuch,
  Kennedy, White, Thomas, Harlan, Marshall, Powell, ...). AZ 280→256, NH 69→64.
  **MN was clean (0)** — its roster was built with full names, so the extractor
  never orphaned cited surnames there. Cull signal used: surname matches a
  SCOTUS justice AND (distinctive name OR no full-name "Judge <First> Surname"
  author byline in any of the judge's opinions). NB the extractor had even
  given some (Scalia, Brennan) bogus MAJORITY_AUTHOR votes — mis-attributed
  authorship — so "never authored" alone was NOT a safe filter; the surname +
  byline test was.
- [x] **ROOT BUG FIXED 2026-07-27 — phantoms will no longer recur.** Two
  extractor sources, both closed in `resolve_judges.py`:
  (1) the AZ top-of-opinion byline block matched body descriptions of OTHER
  courts' opinions ("Justice Scalia authored a dissent, in which Justice Thomas
  joined") — now anchored to THIS court's own attribution ("...of the Court" /
  "the Court's opinion"; both real AZ phrasings, incl. McMurdie/Williams'
  possessive form). Verified: recovers all 9,158 real bylines, author unchanged
  on every shared match, drops only citation-shaped blocks.
  (2) the NH-style footer path ("SURNAME, JJ., concurred") is structurally
  IDENTICAL to a parenthetical SCOTUS-lineup citation — measured across the
  corpus, NOTHING structural separates them (verb form, position, last-match all
  fail; real AZ signoffs use the participle "concurring" too). Added
  `_CROSS_COURT_JUSTICES`, a 55-surname stoplist built by subtracting any SCOTUS
  surname that collides with a real MN/NH/AZ judge (protects genuine locals: NH's
  Souter — an actual NH justice before SCOTUS — plus AZ Miller/Stevens, MN
  Murphy). Applied ONLY to the weak footer path, never the corroborated byline
  block. Also culled one straggler (AZ "Alito", 2 votes) + added alito.
  **Residual (accepted):** surnames shared with a REAL local judge (stevens,
  souter) can't be stoplisted, so a cited Justice Stevens/Souter still inflates
  Henry S. Stevens / David Hackett Souter — inherent surname ambiguity, small +
  bounded. `resolve_judges --state AZ` is now safe to re-run once deployed.
  **VERIFIED at scale 2026-07-27:** re-ran `resolve_judges --state AZ
  --create-missing` over the full corpus (7 cull-safe chunks, `--max-runtime 35
  --id-batch 6000`) → **zero judges with a stoplisted surname minted**; AZ now
  271 judges / 29,742 panel votes, top surname-only rows all real (Lopez,
  Struckmeyer, Vásquez, Gordon, Prade, ...). Driver needed a retry-on-2013
  wrapper (shared DB drops the raw_text fetch connection intermittently, even
  off-window).
- [x] **PDF-hyphenation judge dupes MERGED 2026-07-27.** New idempotent command
  `merge_hyphenated_judges` (dry-run by default, `--apply` to commit): a
  hyphenated row is merged into its clean twin ONLY when de-hyphenating the
  surname matches another judge in the same state, so a genuine hyphenated name
  (`Muriel Jeannette Smith-Florez` — correctly left alone) is never touched.
  Votes reassigned with (opinion, judge)-collision dedup keeping the stronger
  vote type. Merged **8 AZ rows** (Struck-meyer, Holo-han, Decon-cini,
  Dono-frio, Eu-bank, Ge-sell, Mc-farland, Win-des) → AZ 271→263 judges, 59
  votes moved. Reusable for MN/NH.
- [x] **Residual same-judge dupes MERGED 2026-07-27.** New command
  `merge_duplicate_judges` (dry-run default, `--apply`) handles two classes per
  surname group, ONLY when the surname has a single unambiguous full name in the
  state: exact-name twins and bare-surname shadows both fold into the canonical
  full-name row. Cleaned **9 rows** — AZ 6 (Deconcini/Donofrio/Eubank/Mcfarland/
  Windes shadows + a duplicate William A. Holohan), MN 2 (duplicate Natalie
  Hudson + Renee Worke roster-vs-vote rows), NH 1 (a `Hantz\nmarconi` newline
  artifact). AZ 263→257, MN 124→122, NH 64→63. **Key safety fix:** merges carry
  editorial metadata FORWARD (bio/portrait/roster status/appointment/CL-id) and
  pick the metadata-rich row as survivor, so a seated bio row is never deleted
  in favor of a bare vote stub — Hudson's 2808-char bio + seated status kept,
  the 32 votes moved onto it. Shared merge machinery lives in
  `opinions/judge_merge.py` (used by both merge commands). Conflicting-first-name
  surname groups (genuinely different judges) are reported + skipped, never
  guessed.
- [x] **NH roster deep-clean 2026-07-27 (63→37 judges).** Fixing Hantz Marconi
  turned into a full NH roster audit that found four artifact classes, all now
  resolved: (1) **name/casing** — `Hantz marconi`→`Hantz Marconi`, role set
  ASSOCIATE_JUSTICE (she's a FORMER justice, correctly not on the 5-seat current
  roster: MacDonald/Gould/Will/Countway/Donovan); (2) **OCR corruptions** of
  prolific justices merged back by byline confirmation — `Hlcks/Iiicks/Iilcks`→
  Hicks, `Üalianis/Dallanis/Balianis/Daliants/Daljanis/Dallajntis/Dauianis`→
  Dalianis, `Dtjggan`→Duggan, `Brook`→Brock (recovered ~65 votes); (3)
  **citation/non-judge false-positives** deleted — `Tjoflat`(11th Cir),
  `Ripple`(7th Cir), `Geske`(WI), `Connor`(=O'Connor SCOTUS), `Gudas`(=NH clerk
  Timothy Gudas), `Affirmed`(disposition word), plus the `Clerk\n…`/`And …`
  footer-block artifacts; (4) **suffix-split dupes** — `Broderick`(1021v)→
  `John T. Broderick jr`, Horton/Thayer likewise. The suffix split exposed a
  `surname()` bug (it took the trailing "jr"/"3" as the surname) — FIXED in
  `opinions/judge_merge.py` to skip generational suffixes, and `_norm` now
  drops periods/commas so "Jr" == "Jr.". Re-running merge_duplicate_judges with
  the fix also recovered **432 AZ votes** from suffix-splits the first pass
  missed (Lopez→John R. Lopez IV, Gordon→Frank X. Gordon jr, Struckmeyer's three
  rows, Brammer, Patterson). **Remaining NH enrichment (optional, needs a
  source):** Hantz Marconi's first name — **CL verification FAILED 2026-07-27:
  she is NOT in CourtListener's people DB** (search `q=Hantz`/`q=Marconi` and
  filter `name_last=Hantz`/`Marconi` all returned 0; CL's people index is
  FJC/federal-heavy and doesn't carry this NH *state* justice — Souter is in it
  only because he reached SCOTUS). Left as the verified `Hantz Marconi`; a first
  name would need an authoritative non-CL source (NH Judicial Branch roster) and
  must NOT be fabricated. The mid-1900s 0-vote historical roster rows
  (Blandin/Lampron/Kenison/…) are real justices with no in-corpus opinions,
  left as-is.
- [x] **Holdings page-number artifacts.** ✅ DONE 2026-07-24. `holdings.py`
  now strips stray PDF page numbers verbatim-safely — the data showed ~half
  the flagged cases were *legitimate* numbers ("subdivision 6", "51 years"),
  so the fix defaults to KEEP and only drops a bare mid-sentence integer with
  no "real number" signal around it (proper noun / enum noun / month before;
  unit / plural / range after). Verified 10/10 stray removed, 21/21 legit
  preserved, re-extracted all 3 states. **Known limitation (deliberate):** a
  small tail of spurious numbers before a plural noun ("substantial 10
  rights") is left alone — indistinguishable from a real count ("5 factors"),
  and a wrong strip is worse than the noise.

## Tier 2 — automation (turn finished work autonomous)

All member-panel / logged-on — Onion registers, not scriptable by me. Both
scripts verified present + working on prod 2026-07-25 (freshness_check ran
clean, exit 0). NFSN emails a scheduled task's stdout/stderr to the account.

### NFSN scheduled tasks — Manage Site → Scheduled Tasks → Add

Copy-paste fields (Tag / Command / Schedule):

- [x] **`aicitations`** + **`freshnesscheck`** — Onion fixed both in the member
  panel 2026-08-04. Script verified working the same day:
  `freshness_check.sh` exits 0 and prints all three states fresh.

Registered + running: `embedtick`, `heartbeat`, `precomptags`, the five
`ingest*` CL jobs, plus the two above.

**The real 2026-08-04 finding — the alarm had NEVER fired.** The panel showed
`freshnesscheck` running `me/private/docketdrift/scripts/freshness_check.sh`,
still missing the leading `/ho`, months after that fix was recorded as done in
CLAUDE.md. "Last Run" was populated the whole time, because **a task that fails
instantly still records a Last Run** — so the panel looked healthy while the
staleness safety net for every per-state scraper was dead. Do not read
Last Run as evidence a task works; run its script by hand.

**Both scripts are mode `-rw-rw-r--`, NOT executable**, so any task command
must start with `/bin/sh`. Path alone is not enough.

**Two CLI gotchas worth keeping** (`nfsn` on prod, not just the member panel):
1. `nfsn set-cron` / `add-cron` **do NOT validate argument count** — a call
   with a missing command silently succeeds and creates a task. There is **no
   `delete-cron` verb**, so a mistake can only be undone in the member panel.
   Never probe these verbs to discover their signature.
2. `test-cron` rejects any tag containing a hyphen ("Tag must be alphanumeric"),
   so it **cannot see** the real tasks — `embed-tick`, `cron-ingest`,
   `ai-citations`, `freshness-check` all fail the existence check while running
   fine. A failed `test-cron` is NOT evidence a task is missing.

### Windows Task Scheduler (residential box, NOT NFSN)

- [x] **`run_mn_weekly.ps1`** — DONE. Verified registered 2026-08-03:
  `\DocketDrift MN COA weekly scraper` (Interactive only), alongside
  `\DocketDrift NH weekly scraper`. MN COA forward-fill is autonomous.

## Tier 3 — coverage (bigger builds)

- [ ] **Louisiana rollout + FLP report #2** (queued behind landing MN with
  FLP — pacing is deliberate). The every-state audit
  (`docs/cl_coverage_audit/FINDINGS.md`, 2026-08-06) found LA Supreme DEAD in
  CL since 2020 (~12K missing, largest single-court hole in the country)
  while LA COA is intact — so the COA foundation comes from CL bulk and the
  Supreme needs a direct lasc.gov source from day one. Verification of the
  hole for FLP doubles as rollout recon. 16 other courts are flagged in the
  findings doc; Nevada carries the cleanest unpub-died signature if a
  non-rollout report is ever wanted first.


- [~] **MN backfill — LARGELY DONE 2026-08-04. Two gaps left, both small.**

  **Done:** 2020/2021/2022 rebuilt from 0 → 1,040 / 1,092 / 970; 2017 438 →
  1,350; 2018 208 → 1,327; 2019 176 → 856. ~5,900 opinions, all with
  dispositions (98%), statutes, holdings, panel votes, stored PDFs, and
  text-extracted citation graphs. Reconciliation clean (2020–22 missing 5 of
  3,108, all diagnosed; 2017–19 missing 0 of 3,311).

  - [x] **2019 H2 — DONE 2026-08-05.** 632 opinions, 0 windows skipped, 0
    CAPTCHAs. 2019 856 → 1,431. The earlier block was TRANSIENT, not a burned
    profile: a 4-week probe the next day walked straight through. **Probe with
    one short window before concluding the wall is up.**
  - [x] **2023 — DONE 2026-08-05.** 1,018 swept (0 skipped, 0 truncated),
    1,016 ingested; 115 → 1,014. The 2 misses are Supreme PDFs that errored on
    parse, named by the reconciliation.

  - [ ] **2024 + 2025 — SAME UPSTREAM GAP, DIAGNOSED 2026-08-05. ~2,400
    opinions.** Not CL lag and not our pipeline: **CourtListener's MN Court of
    Appeals ingestion is STILL BROKEN, and degrading.** Live API `count=on`:

    | minnctapp | CL live |
    |---|---|
    | 2016 (control) | 1,231 |
    | 2024 | 463 (38%) |
    | 2025 | **149 (12%)** |

    Our DB matches CL almost exactly (2024: 550 vs 556 bulk; 2025: 238 vs 242),
    so nothing was dropped on our side. The bulk export's precedential split
    names the mechanism: 2024 = 381 Published / 175 Unpublished, against an MN
    norm near 1,100 unpublished a year — **the UNPUBLISHED stream is what CL is
    missing**, the same signature as 2017–2019 (2018 came through
    Published-only).

    So the documented story ("2020–2022 empty, 2017–2023 thin") is wrong in
    scope: CL's MN coverage has been degraded continuously from 2017 to the
    present and is currently at its WORST. It only read as "recent years still
    filling in" because the older hole was total enough to dominate attention.

    **Our own pipeline is fine** — proof: our 2026 count (232) EXCEEDS CL's
    (128), because the weekly archive scraper has been adding since July. The
    scraper works; it just only moves forward and was never going to backfill
    what CL missed in 2024–2025.

    Fix: the same archive sweep, ~100 weekly windows, ~90 min attended, plus
    early 2026 up to whenever the weekly scraper started.

    **Also worth telling FLP** — their issue is scoped to 2020–2023, but "the
    scraper is still broken today, at 12%" is a materially different and more
    actionable report than "there was a hole three years ago." 

  **Do NOT skip the reconciliation step** on either. A skipped window is not an
  empty one: 2019 came back at 803 and looked plausible, and only the SKIPPED
  count revealed that half the year was never collected.

  Ingest into these years is now safe because `normalize_case_numbers` ran —
  before that, 2023's 115 rows were 115/115 malformed and every one would have
  duplicated rather than skipped. Measured proof: the 2017–19 ingest skipped
  598 already-held opinions and created 2,772, with 0 duplicates.
  **The working recipe** (`scripts/mn_scraper/backfill_mn_archive.py`):
  the search's `start-date`/`end-date` params are silently ignored, but the
  filename carries the filing date (`OP<case>-<mmddyy>.pdf`) and Vivisimo
  indexes it, so **`--strategy filedate` queries one Monday at a time** and
  every result verifiably lands on that date (10/10 and 29/29 and 7/7 on the
  three days tested). MN files on Mondays, so a year is ~52 queries.
  ```
  python scripts/mn_scraper/backfill_mn_archive.py --strategy filedate \
      --since 2021-01-01 --until 2021-12-31 --no-download --manifest mn2021.tsv
  # then curl the manifest URLs from NFSN (PDFs are NOT walled there) and
  # ingest_pdfs --state MN --court appeals   (and --court supreme)
  ```
  Listing-only + server-side download is the fast shape: ~25s of browser time
  per Monday instead of ~2min. Budget roughly 2.5–4h of attended browser time
  for 2017–2023, chunked by year.
  **THE OPEN RISK — completeness.** The window check proves every result is
  *in* the requested day; it CANNOT prove we got *all* of that day. One tested
  Monday returned 29, the next only 7 (confirmed against a second query shape,
  so it's the index's own answer, not our filter). After each year, verify:
  total vs the ~1,400/yr historical norm, and case-number density across
  `A<yy>-####`. A year landing at ~400 means silent under-collection, not a
  quiet docket.
  **BLOCKER CLEARED 2026-08-04** — `normalize_case_numbers --apply` renamed
  **41,318** dockets across all three states; filename stems are now 0
  remaining (was 942 in MN). Verified after: all four spellings of a docket
  resolve (canonical / stem / `No. ` / unpadded), a bogus docket still 404s,
  and the citation graph still resolves at 91% in MN and NH. What remains
  malformed is the collision set, deliberately untouched (MN 788 / AZ 674 /
  NH 71 `NO. ` prefixes, 57 unpadded) plus the 14,436 `cl-<id>` rows, which
  need CourtListener's API rather than string work. The original writeup of
  the blocker follows.

  **BLOCKER FOR THE THIN YEARS ONLY (2017–2019, 2023) — found 2026-08-03.**
  Those years already hold rows whose `case_number` is a malformed stem:
  `a230380`, `a250826`, `a221655` — lowercase, unhyphenated. **The defect is
  CourtListener's, inherited on ingest**: CL's own MN docket numbers are stored
  that way (`dockets.csv` shows `A211648`, `a241471`, `a250033`). Our PDF
  parser produces the CORRECT `A23-0380`, and `ingest_pdfs` dedups on an EXACT
  `(court, case_number)` match — so backfilling into those years would create a
  SECOND row per opinion instead of skipping. 2023 is 115/115 malformed; 2025
  is 180/238. **2020–2022 are safe** (empty, nothing to collide with), which is
  why the sweep started there.
  Fix before touching the thin years: an in-place normalization pass rewriting
  `a230380` → `A23-0380` (same class of repair as the 14,428 synthetic
  `CL-<id>` numbers, and worth doing in the same pass — both make pages
  unreachable by the identifier a lawyer would actually paste).
  **Also known and NOT fixable here:** MN Supreme lands at ~53% because the
  law library's opinions archive does not carry Supreme *orders* — attorney
  discipline, Lawyers Professional Responsibility, administrative dockets like
  `ADM10-8032`. CL had those. The backfill recovers Supreme opinions, not
  Supreme orders; say so rather than implying the year is complete.
  **Still true and still worth doing:** report to FLP (draft ready at
  `docs/flp_issue_1115_comment.md`, NOT sent — Onion's call). Note the
  calibration also found **17 appeals opinions in 2016 Q1 that we don't have at
  all**, in a quarter CL considers complete — so this pipeline finds records CL
  missed outside the 2020–2022 hole too.
  **The gap is CourtListener's, not ours.** CL's bulk export, our DB, and CL's
  live API all show MN 2020–2022 = **zero, both courts** (control: minnctapp
  2016 = 1,231). CL's dockets are empty for those years too. **No CL path fixes
  this — don't re-run loaders.** Worth reporting to Free Law Project as a real
  bug (their MN juriscraper looks to have broken around the 2020 mn.gov
  redesign and been backfilled only from 2023).
  The opinions are freely available from mn.gov (verified: 2021 PDFs 200 OK,
  and our MN parser reads them correctly with no changes). **Only enumeration
  is bot-walled.** Needs a *date-windowed* search scraper — the existing
  `scrape_mn_coa.py` has `--since` but no upper bound and stops at pager page
  10, so it cannot reach history; and it must be extended to cover `supct`
  (MN Supreme is missing for those years too). The GET search URL works and
  bypasses the flaky JS form, but `start-date`/`end-date` are ignored, so the
  window has to go in the Vivisimo `query` (`date:>…`) — syntax unverified, and
  the wall CAPTCHAs on the second rapid navigation, so probe it *slowly*.
  ~9,000–10,000 opinions. Big, attended (a human clears CAPTCHAs).
- [~] **CL catch-up ingest — reframed 2026-07-25.** A bounded probe showed
  recent coverage is ALREADY largely current: an AZ COA run over the last ~7
  weeks was created=4 / updated=36 (~90% already present), because the weekly
  cron now lists from `/clusters/`. So there is no big RECENT gap to chase.
  The probe also confirmed the rate-limit trap is real and easy to trigger:
  ~14s per cluster on ingest, and a burst of cheap count queries put CL into a
  heavy backoff (a single count query then took >7 min). Because the API token
  is shared with the weekly cron, hammering it can stall the automated ingest.
  **Conclusion:** don't do a large historical backfill through the REST API.
  The rate-limit-free path is CL's BULK exports offline (`load_cl_bulk` /
  `scripts/cl_bulk_filter.py`) — the same zero-API approach that built the
  reporter cites + 605K-edge citation graph. Reserve `ingest_court` for the
  incremental cron. If a specific historical window is known-thin, a *small*
  `--limit` run with long spacing is fine; blind wide sweeps are not.
- [ ] **Repair the 14,436 synthetic `cl-<id>` case numbers** (MN 6,448 /
  NH 7,848 / AZ 140). Unreachable by the only identifier a lawyer has. **Not
  solvable by string normalization** — the digits are CourtListener's cluster
  ids and no docket is recoverable from the row, so this is the one population
  `normalize_case_numbers` deliberately leaves alone. Needs `fetch_docket()`
  against CL's API, keyed on `courtlistener_id`, written IN PLACE
  (`update_or_create` would duplicate). Mind the rate limit — same token as the
  weekly cron. Medium.

- [ ] **Merge the 1,478 duplicate-opinion pairs** — phase 2 of the case-number
  work, found by the normalization dry run. Both spellings of one docket exist
  as separate rows. Classified on 2026-08-04: **961 "same date, different
  length" + 13 near-certain → probable duplicates; 508 have DIFFERENT dates and
  are two REAL opinions on one docket** (opinion + amended opinion, or opinion
  + order) and **must never be fused** — a blanket merge would destroy 508 real
  documents. Needs the `merge_duplicate_judges` discipline: keep the richer
  row, carry editorial metadata forward, never merge on ambiguity. Medium.

## Tier 4 — throughput & hardening (lower priority)

- [ ] **★ SEARCH UNDER CONCURRENCY — the #1 launch risk, still untouched.**
  Measured 2026-08-02 and unchanged since: uncontended search is fine (MN 2–3s,
  NH 1–5s, AZ 1–2s), but with just **TWO** concurrent search sessions one query
  hit **183s**, reproduced on both AZ and MN, with sibling connections dying on
  errno **188** and **1969** — the documented poison cascade. One gunicorn
  worker × 8 threads. A launch moment (HN / CourtListener traffic) means many
  simultaneous common-term searches, which is exactly this shape. Everything
  else on this list is data quality; this is the one that takes the site down
  in front of an audience. Nothing safe to do in five minutes — needs real
  capacity work (worker count vs the NFSN RAM ceiling, or bounding the
  search path harder).

- [ ] **Triage the ~50K tag-review queue** via the pile-picker admin. Onion.
  **Note:** the ~5,900 opinions backfilled 2026-08-04 embed on the overnight
  tick (`.embed_state` now = MN), and `suggest_tags` must WAIT for that — new
  rows carry a placeholder zero vector until embedded, so scoring them earlier
  produces garbage.
- [ ] **Harden the tag-review heavy slice** — self-bind slice/count queries
  with `SET STATEMENT max_statement_time` + catch-and-close (poison-cascade
  defense), mirroring `semantic.py`.
- [x] **Fix the broken per-page Twitter-card meta.** ✅ DONE 2026-07-25.
  `twitter:title`/`description` used the no-op `{{ self.og_title }}` Jinja
  idiom, so every X card showed "DocketDrift". Fixed by dropping the broken
  twitter:* tags and relying on the (working) og:* fallback — one source of
  truth. Also fixed a latent bug: judge cards now use the judge photo
  (og:image fallback) instead of the generic cover. Verified live on opinion,
  judge, and apex pages.
- [x] **Beta/Flagship labels.** ✅ DONE 2026-07-25. MN is now the Flagship
  (largest, most complete corpus); NH + AZ get an affirmative green "Live"
  pill (new `.status-pill--live` replacing `--beta`). Updated state-landing,
  about pills, and the API-docs prose. Verified live.
- [x] **State-router middleware lookup cache.** ✅ DONE 2026-07-25.
  `_resolve_state` memoizes the subdomain→State lookup by 2-letter code
  (misses included) for the worker's lifetime — no more per-request DB hit.
  Bounded key space, read-only instance (thread-safe), cleared on restart.

## Diminishing returns (only if a frequency scan shows a new cluster)

- NH's remaining ~4,465 no-match dispositions (genuine 19th-c. prose).
- AZ's ~12K no-match dispositions (genuinely historic AZ text).
- These are the pre-modern tail; not worth a pass unless a scan finds a
  matchable cluster.
