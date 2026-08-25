# DocketDrift — working backlog

Snapshot 2026-08-16. Prioritized. Each item says what it is, why it matters,
and roughly how big. "Onion" items need the member panel or are editorial.

**2026-08-10→16 session (full detail in CLAUDE.md's session block):**
**PDF page anchors live** (`#page-N` from pypdf `\f`; AZ 50% modern coverage,
NH 2020s 98%; the rest is CL's upstream gap, not ours — pypdf paths fixed to
emit `\f` going forward). **LA Phase 8a–d DONE**: statutes 252K rows /
citations 1.54M edges (56%) / judges 1,437 rows + 74K votes / holdings 34.6K;
**8e embed IN FLIGHT** (overnight window, ~$100 measured, days not weeks after
the embed ORDER-BY fix — 100× fetch speedup); 8f tags blocked on 8e. **Judge
co-panelist heat chips shipped** (aligned/partial/split on every dossier) —
and the chips exposed that panel votes were ~100% MAJORITY_JOIN, so dissent/
concurrence extraction was built per-state (NH footer re-sweep, MN caption
lines, AZ byline prose + new CONCURRENCE_AUTHOR pass): NH 299 / MN 294+150 /
AZ 131+43, plus +14K recovered MN votes. Two regressions caught + fixed:
`_match_opinions` IN-list (25s KILL on every AZ/MN opinion page) and
`_cohort_with_heat` v1 (2013 on high-vote judges). New ops gotchas in
CLAUDE.md: rc=152/rc=137 wrapper deaths, `qs.iterator()` = client-side
buffering, `daemon(8)` vs `nohup`, cold-cache stampede after restart
(`precompute_explore_tags`), CL cluster-vs-opinion id spaces.

**2026-08-07→08 session (full detail in CLAUDE.md's session block):** search
concurrency cliff CLOSED (re-measured: 8 concurrent = 5.8s, was 183s);
cited-by 500 fixed (1,844 pages, was 49% of all 5xx); `merge_duplicate_opinions`
merged 605 dup pairs (−605 corpus, 4,465 inbound edges rescued); cl-`<id>`
resolved (CL empty upstream — 24 AZ from captions, MN/NH covered by
reporter_cite); tag-review hardened + a 20.6s FORCE-INDEX regression fixed;
MN early-2026 backfilled (519); **AZ judges rebuilt 247→194 + root extractor
fix + Court split into COA Div One/Two + 35-judge current bench seated across
3 courts** (from the courts' own rosters via the in-app browser); NH landing
re-pitched (free/private/verbatim) for the NH Legal Aid presentation; **MN
2017–2026 corpus PUBLISHED to archive.org** as a zip. New open items surfaced:
5xx-rate alerting (nothing watches it — cited-by 500'd for 12 days unseen),
same-court-second-decision drop at ingest (Tier 4), IA loose-PDF cleanup
finishing in a background pass.

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

**FOUR BROKEN PAGE TYPES FOUND + FIXED 2026-08-06b — none of them by a
monitor.** A search-concurrency re-measurement (which came back clean) tripped
one crawler 500, and pulling that thread found: `cited-by` hard-500ing on
**1,844 pages for 12 days** (bare `.get()` on a docket, which isn't unique
after appeal — `_pick_opinion` existed since July but this view shipped later
and never got it); the same view dragging **1.4MB of `raw_text` per page** it
never rendered (intermittent errno 2013); **every state's sitemap having a
500ing chunk** — NH's ONLY chunk, AZ's chunk 2, MN's chunk 3, all ~27s past the
25s cap, because one `release_date` column for `<lastmod>` knocked the query off
the covering index (27.0s → 0.08s once dropped; `<lastmod>` was `release_date`,
which never changes, so it signalled nothing); and **117 AZ dockets containing a
slash** (`CV-24-0222-AP/EL`) that `<str:>` could not reverse, 500ing whole judge
pages. All fixed, verified, and now watched by the new 5xx monitor (Tier 4).
**The lesson worth keeping: all four were invisible to every existing check,
survived a full site audit, and were found only by reading the access log.
Data-freshness monitoring cannot see a page that doesn't render.**

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
- [x] **`/how-we-differ/` extractive-holdings section — SHIPPED 2026-08-02**
  (`#holdings`, "The one panel that looks generated, and isn't"); the holdings
  panel link on opinion pages was repointed at `{% url ... %}#holdings` in the
  same session. Cleaned up a stale contradictory template comment 2026-08-08
  (opinion_detail.html:165 still said "NOT yet documented" months after the
  fact — CLAUDE.md was right, this item was a stale TODO).

## Court-level modeling — AZ divisions split (2026-08-07)

- [x] **`Court.division` added; AZ COA split into Division One / Two.** Decided
  while at 3 states so the model doesn't need repainting after growth. Court
  identity is now `(state, level, division)` — empty for single-court levels
  (any Supreme Court, MN's unified COA), populated for multi-panel systems
  (AZ Div 1/2; ready for CA's 6 districts, TX's 14 COA). `assign_az_divisions`
  (idempotent, dry-run default) made the combined `arizctapp` row Division One,
  created Division Two (`arizctapp-2`), and reassigned by docket prefix:
  **Div One 18,593 opinions / Div Two 5,717**, 37 genuinely-malformed dockets
  (0.15%) left on Div 1 and reported, never guessed. Judges re-homed to their
  majority division (10 → Div 2), with current Supreme justices who once sat on
  the COA (Cruz/Beene/Timmer/Montgomery) correctly EXCLUDED. The slim embedding
  table's `court_id` (part of its clustered key) was updated in lockstep — Div1
  18,575=18,575, Div2 5,717=5,717 in sync. Opinions stay ONE unified searchable
  corpus (court is a facet, not a fork); AZ state search still spans both.
  **Run `assign_az_divisions` after any AZ COA ingest** — the CL feed keyed on
  `arizctapp` lands new opinions on Div 1, and this re-homes the Div 2 ones.
- [x] **AZ sitting justices corrected (8→7).** Lopez seated as Vice Chief (new
  `Role.VICE_CHIEF_JUSTICE`); the 2 misflagged COA judges unseated; all 7 carry
  self-hosted photos + `bio_url` (now rendered as an "Official bio ↗" link on
  every judge card — it was in the DB but the template never output it).
  Sourced from azcourts.gov/MeettheJustices (Akamai-walled to server fetches;
  the in-app browser got through). Landing cache needed clearing to show 7.
- [x] **AZ judge roster 247 → 194** across two cleanup tiers + the root
  extractor fix (parenthetical-citation + non-name guards in `resolve_judges`,
  so leaks/junk don't recur). See below + CLAUDE.md.

## Tier 1 — editorial polish (the honest asterisks on the dashboard)

- [x] **AZ roster cleanup Tier 1 — DONE 2026-08-07 (247 -> 202).** Root of the
  "AZ has 2x the judges of MN/NH" mystery: three extraction defects, not real
  judges. `cleanup_az_judges` (one-shot, dry-run default, verified verdicts as
  explicit pk lists, name-checked + curated-metadata-guarded) actioned the
  high-confidence tier: **11 junk-token deletes** (And/Appel/Opinion/State/M
  /Silent/One/Hon/Ini/Judge/Trade), **24 cross-court citation-leak culls**
  (each a parenthetical `(Name, J., concurring)` cite to another court —
  Kozinski/9th, Dietzen/MN, Titone/NY, Sealia=Scalia, Ginsberg=Ginsburg…),
  **10 dist-1 OCR merges** into real rows (Údall/Udaljl→Udall,
  Donoprio/Donofrlo→Donofrio, Eockwood/Lockwoód→Lockwood, Bindes→Windes…).
  0 orphan votes after; survivors render. **The dry-run caught its own two
  false-positive directions before apply:** a naive "SURNAME, J., concurred"
  rule nearly culled the real 19th-c. Territorial justices (Tweed/Sloan/
  Stilwell/Pinney — bare panel signoffs, KEPT), and dist-2 "OCR" matching is
  noisy (Prade→LaPrade right, but Fink→King / Olson→Nelson / Arabian→Fabian
  wrong — Arabian's a *California* justice). The clean discriminator turned
  out to be **is-the-name-parenthetical** (citation) vs bare (panel signoff).
  **Left for the human roster pass (Tier 2/3):** the ~24 dist-2 candidates
  (per-row confirm), the Territorial justices' OCR clusters (Pinney/Pinnei/
  Pxnney/Pokteb → one Pinney), the Flórez/Florez/Floréz accent cluster, and
  byline-real surname-only rows that just need a full name (Vásquez 139,
  Staring 74, Sklar/Eppich 40, Brearcliffe 39).
  **ROOT CODE FIX — DONE 2026-08-07.** `_extract_generic_byline` now carries
  two name-agnostic guards: `_inside_open_paren` skips any footer/dissent match
  sitting inside an unclosed `(` (a `(Name, J., concurring)` citation, not a
  panel signoff — catches EVERY cross-court leak, not just the SCOTUS names the
  stoplist covered), and `_valid_surname`/`_NON_NAME_TOKENS` reject
  connective/role/party tokens + single letters (And/Appel/Opinion/State/M) at
  every capture point. Integration-tested on prod: the Tjoflat + Dietzen
  citation opinions now extract nothing; a real Vásquez byline and a bare
  Territorial Tweed signoff still extract. So leaks/junk no longer recur — a
  future `resolve_judges --state AZ` is safe. (Does NOT fix OCR-variant
  re-minting — inherent to bad scans; that stays a human-pass concern.)
- [x] **AZ phantom-author cull + DeConcini merge — DONE 2026-08-07.** The
  survey found the July cull left THREE stale citation-author leaks behind:
  **Connor / Souter / Burger** were recorded as the *author* of ~12 AZ
  opinions where the name only appears deep in the body as a SCOTUS citation
  (`(Souter, J., concurring)`, `Justice O'Connor observed…`). Root cause was
  narrow and confirmed: `oconnor` is stoplisted but the apostrophe-stripped
  `connor` isn't, `souter` is deliberately unstoplisted (to protect NH's real
  Souter), and the byline path never consulted the stoplist anyway. Verified
  all 18 votes are deep-body citations (offset 8k–80k, never a top byline),
  and — the key check — the CURRENT byline regex (post-July "of the Court"
  anchor) re-mints **zero** of them, so these were purely stale pre-fix rows
  and **culling needs no code guard**. Also merged **"Concini" (42 votes,
  1949–50 = Evo DeConcini's AZ Supreme tenure)** into the existing
  **"Evo Anton DeConcini"** row (a "De Concini" surname-split, distinct from
  the hyphenation dupes the July pass caught) → 46 votes, 0 collisions.
  AZ judges 251 → 247. DB-only, verified: 0 orphan votes, culled rows gone,
  DeConcini page 200.
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

- [ ] **MCP server — put DocketDrift inside Claude as a tool** (NEW
  2026-08-17; supersedes ROADMAP Phase 21 "public read API" — MCP is the
  modern shape of that feature). Context that motivates it: the 2026-08-17
  AI-traffic readout showed ChatGPT-User at ~100 live fetches/day and
  OAI-SearchBot/PerplexityBot actively indexing us, while Anthropic's
  agents are absent (1 claude-user fetch, 0 Claude-SearchBot) — their
  search stack discovers slowly and there is no submission console. The
  crawler path to Claude is passive-only; the ACTIVE path is a connector.
  A read-only MCP server makes DocketDrift a tool any Claude Desktop /
  Claude Code / API user can attach — and the product pitch is exactly
  MCP-shaped: "ground legal answers in verbatim official text;
  hallucinated citations architecturally impossible."

  **v1 scope (read-only, no auth):**
  - `search_opinions(query, state, court?, year_range?)` — the existing
    POST search path (keyword + semantic), returning docket/title/date/
    court + canonical URL per hit
  - `get_opinion(docket, state)` — full text + metadata + citation
    treatment summary; the `_pick_opinion` sibling logic applies
  - `lookup_citation(cite)` — reporter-cite / docket paste-through
  - `get_judge(slug, state)` — dossier stats (role summary, alignment/
    split cohort, disposition lean)
  - `citing_opinions(docket, state)` — inbound edges w/ treatment +
    verbatim citing passages
  - `get_statute(slug, state)` — opinions citing a statute

  **Constraints (product posture, non-negotiable):**
  - Read-only, no accounts, no per-user state. MCP queries are exactly
    the research trail the privacy promise protects: process in memory,
    never log, never persist. The Privacy page must gain an MCP section
    saying so BEFORE launch, not after.
  - No generation anywhere — the server returns verbatim text +
    structured metadata only, same as the site.
  - Serve from the existing gunicorn app (a `/mcp` streamable-HTTP
    endpoint) — no second daemon on NFSN. Rate-limit generously but
    cap concurrent semantic searches (single-worker reality).
  - Cost estimate before build: semantic search per MCP call hits the
    same Voyage-embed + cosine path as the site; verify the process-
    local query-embedding cache is enough at expected volumes.

  **Distribution (the actual point):** submit to Anthropic's MCP
  connector directory; README + llms.txt mention; a "Connect DocketDrift
  to Claude" page on the site with the one-line config. Sizing: the
  endpoints map ~1:1 onto existing views' query helpers, so v1 is
  mostly plumbing + the directory submission — a focused 1-2 days,
  worth doing after LA ships (four states in the connector beats three).

- [~] **Louisiana rollout — Phases 1–8d DONE (2026-08-08→16), remainder
  below.** 341,064 opinions loaded (CL bulk), 5 COA circuits assigned via
  `assign_la_circuits` (52,840 moves; 48% no-signal tail stays on 1st Cir —
  Tier-3 panel-byline classifier is the follow-up), statutes 252,375 rows,
  citations 1,543,411 edges, judges 1,437 (172 seeded + ~1,265 UNKNOWN for
  editorial review), panel votes 74,222, holdings 34,624. What's left:
  - [~] **8e embed — throughput fixed 30× on 2026-08-25; finishes ~1-2
    overnight windows.** The ~7K/night mystery was measured to two causes:
    (a) LA's ~111K permanently-pending pre-1980 rows (out of scope under
    `.embed_since=1980-01-01`) sit at the FRONT of the (embedding_pending,
    court_id) index, and every 256-row batch re-read + discarded that block
    → 0.4 op/s; (b) the startup + end counts carried raw_text/--since row
    predicates and had regressed to 112s+ each, twice per tick. Fixed in
    `embed_opinions` (`535e0ed`): per-court pk-cursor fetch with FORCE
    INDEX (skip rows read once per run, not per batch) + index-only counts
    (0.27s). Measured after: **12.5 op/s** (post-1980 LA docs are short,
    ~647 tok/op) → remaining ~161K ≈ 3.6h runtime ≈ **~$13**, not $60.
  - [ ] **8f suggest_tags** — $0 (pure MariaDB cosine), run after 8e.
  - [ ] **7b lasc.org Supreme backfill — RECON DONE 2026-08-25; hole
    re-verified REAL; unattended build.** Our DB: LA Supreme ~2,000-2,250/yr
    through 2018, then **2020=8 / 2021=0 / 2022=0 / 2023=11 / 2024=42 /
    2025=49** — the 2026-08-19 "feed is NOT dead" correction only proved a
    ~50/yr trickle (~97% missing), so the audit's ~12K figure stands.
    lasc.org recon: **NO bot wall** (datacenter curl gets 200s, fast); site
    is **Blazor Server over SignalR** — no REST API, no prerendered HTML
    (a curl of a batch page returns the 14KB shell), robots.txt falls
    through to the SPA shell; a GoogleCaptcha component exists but is
    likely form-only. **Enumeration key: `lasc.org/opinions?p=YYYY-NNN`**
    (also `actions?p=`, `rehearings?p=`) — batch numbers are sequential
    per year (~60/yr), so 2020–2025 ≈ 360 browser page loads; sitemap.xml
    (real XML, 2,684 URLs) confirms the scheme but only covers 2026.
    Build shape: residential Playwright LISTS (headless fine — no wall,
    no CAPTCHAs expected) → manifest → NFSN downloads PDFs →
    `ingest_pdfs --state LA --court supreme`. ~1 day, unattended.
    NOT a launch gate — disclose the 2020–2025 Supreme gap on /about/
    (MN precedent) and backfill after launch.
  - [ ] **9–12: rosters, weekly cron, flip is_live** — seat the 5-circuit +
    Supreme benches, register the weekly forward-fill, then LA goes on the
    nav. `docs/LA_BUILD_LIST.md` has the full overlay.
  Reporter/parallel cites: NOT COMING from CL (their citations.csv has zero
  rows for LA's 10M+ cluster ids — verified against live API; permanent
  upstream gap until CL backfills).


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

  - [x] **2024 + 2025 — DONE (was already done; this box was stale).** The
    2026-08-05 evening sweep that closed 2019 H2 + 2023 also swept 2024–2025
    (the header table's 1,161 / 1,133 ARE the post-sweep numbers; a
    2026-08-06b monthly census re-confirmed them live). The diagnosis below
    stands as the FLP-report evidence — CL's live MN COA feed remains at
    12–38% — but there is no remaining backfill work in these years.
  - [x] **Early 2026 — SWEPT + INGESTED + RECONCILED 2026-08-06b.** Jan–Jul
    2026 was the last thin span (held ~234 vs ~700 norm; the weekly scraper
    only reaches back to mid-July). Attended sweep: 30 windows, 1 CAPTCHA,
    **747 collected**; one window skipped twice (Jan 1–2: New Year's
    Thursday + Friday, no Mon/Tue/Wed filing day — accepted as empty,
    ~1–2-opinion risk). NFSN fetch 746/746 (0 failures) → **519 created /
    227 already held / 0 errors**. Derived passes ran same night: statutes,
    holdings (+418), judges (+238 votes, completion verified with a second
    pass), citations (**6,451 edges** over exactly the 519). Reconciliation:
    **736/746 in DB, 0 missing**; 10 date mismatches = 5 filename-vs-Filed
    conventions + 5 same-court second decisions the schema cannot hold (new
    Tier-4 item). Embed: 559 pending, `.embed_state`=MN, overnight tick.
    `suggest_tags` NOT run — waits for the embed (placeholder-vector rule).
    Original diagnosis kept below for the FLP thread:

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
- [x] **cl-`<id>` case numbers — RESOLVED 2026-08-06b, and the premise was
  wrong.** The planned `fetch_docket()` repair would have burned ~14,400 API
  calls for **zero** recoveries: joining all 14,436 rows against the local CL
  bulk subsets (offline, zero API) showed every cluster present and **14,416
  with an EMPTY docket_number in CL's own dockets table**; a 3-call live-API
  probe confirmed upstream is still empty today. CL genuinely has no docket
  for these. What actually happened instead:
  - **AZ: 24 repaired from OUR OWN text** — modern opinions whose caption
    carries the docket (`No. CV-08-0225-PR`). Extraction anchored to the first
    2,500 chars, court/docket-shape alignment guard, collision-checked,
    verified live. 36 more extracted a docket another row already holds —
    true duplicate pairs, handed to `merge_duplicate_opinions`.
  - **MN + NH: nothing to repair, and no reachability hole.** Their cl- rows
    are 1860s–1930s reporter-OCR opinions — no caption, no docket, in text or
    upstream. But **MN 100% / NH 99% of them carry a `reporter_cite`**, which
    IS the identifier a lawyer pastes for a 19th-century case. The cl- URL is
    cosmetic there, not a gap.
  - Residual: ~80 AZ misses (62 territorial-era + bar-discipline/OCR-mangled
    captions) and the ~20 real `Cl-##-####` MN dockets the old count
    mistakenly included (case-insensitive LIKE). Nothing further to do.

- [x] **Duplicate-opinion pairs — MERGED 2026-08-06b: 605 pairs.** New
  `merge_duplicate_opinions` command (dry-run default, --apply, --limit,
  --pairs-file), built with the `merge_duplicate_judges` discipline. Three
  gates — same court, same release_date, token-containment title match — and
  every refusal is reported, never guessed. 581 canonical-collision pairs +
  24 AZ cl-caption pairs merged; **4,465+ inbound citation edges re-pointed**
  (a bare delete would have cascaded them away), scalars fill empty survivor
  fields only. Verified after: corpus exactly −605 (127,563), **zero orphaned
  FKs across all seven referencing tables, zero self-edges, zero duplicate
  (court, case_number) keys**, survivors render 200.
  **Remaining 906 + 12 are deliberate refusals, not backlog debt:** the
  different-date pairs are two real documents on one docket (opinion +
  amended/order — never merge), and the title-conflict pairs include
  same-serial rows that are *genuinely different cases*. One reviewable
  sliver: ~7 AZ cl- pairs skipped on abbreviation-artifact titles (`Adot` vs
  `DEPT. OF TRANSP.`) — same case, needs 2 minutes of human eyes; rerun with
  `--pairs-file` after confirming.
  **Two shared-DB traps hit en route, both now in the command:** a DELETE on
  the slim embedding table must address the full clustered PK (no secondary
  index → a bare opinion_id predicate locks all 128K rows, errno 1206); and
  chunk drives must expect intermittent 2013 drops (per-pair transactions
  make a died chunk cleanly resumable — chunk 5 picked up chunk 4's remainder
  exactly).

- [ ] **Pre-1912 AZ OCR-failure sweep — recover Territorial-era hidden votes**
  (queued 2026-08-08 after the Pokteb / Pobteb / Porter case). The byline
  extractor gives up on badly-mangled Territorial-era OCR ("Pobteb" for
  "Porter", "Pokteb" for the same person from a different scan). Cole v.
  Bean (1878, cl-6597441) was silently missing Porter as a panel member;
  we only caught it because Onion happened to eyeball the opinion. Same
  failure mode almost certainly hides participation across other 1870s-
  1890s AZ opinions — anywhere the OCR is bad enough to defeat the byline
  regex but the actual role text (`J., concurred`, `J., dissented`,
  `J., expressed no opinion`) is still parseable. Fix shape: scan pre-1912
  AZ opinions (that era only) for unmatched-name-followed-by-role-text
  patterns, compute nearest-Levenshtein-distance to existing Territorial
  Judge slugs (Tweed/Porter/Sloan/Pinney/Barnes/Stilwell/Kibbey and the
  handful of others), and either add a PanelVote to the matched judge
  (for a high-confidence match) or emit a review candidate for manual
  triage. Low-priority — the entire pre-1912 AZ corpus is small (a few
  hundred opinions) and the votes are historic — but the fix pattern is
  simple and reusable for MN early-territorial too.


- [ ] **Panel-byline circuit classifier for LA COA no-signal tail**
  (queued 2026-08-09 after Phase 7c apply). assign_la_circuits' scan
  caught 51%% of opinions via header + parish + JDC signals; the
  remaining 48%% is mostly OCR-mangled writ dispositions where the
  ONLY clean circuit signal is the panel byline (``BEFORE: WOLFE,
  MILLER, GREENE, JJ.``). Fix pattern: compile a per-circuit
  judge-surname map from LA COA rosters (~50 names across 5 circuits),
  match ``BEFORE: X, Y, Z`` blocks, vote by majority-circuit of the
  named panel. Risks: judges who moved between circuits over decades
  pollute the map (need era-aware assignment); rosters aren't
  cleanly listed on any single .gov page. Alternative: use CL's
  people DB once resolve_judges (Phase 8) mints LA judges with
  court FKs -- then this signal is free.

## Tier 4 — throughput & hardening (lower priority)

- [x] **Same-court second decisions on one docket — FIXED 2026-08-08
  (migration 0036 + ingest fixes + read-side ?on=).** Unique key widened
  from `(court, case_number)` to `(court, case_number, release_date)`, so
  opinion + amended-opinion and opinion-on-remand coexist on one docket
  (the COA/Supreme case was already fine because court differs). Both
  loss modes closed: `ingest_pdfs` no longer SKIPs the second document,
  `ingest_court.update_or_create` no longer OVERWRITES the first with
  the second's fields. Read side mirrors the existing COA/Supreme sibling
  UI: `_pick_opinion` gains `?on=YYYY-MM-DD` and defaults to newest
  release_date within a level; the "Also decided on this docket" panel
  lists date + appends `on=` to the sibling link. **De-risk before
  migrating:** 0 collisions on the proposed 3-column key across the whole
  128K corpus, so the constraint held cleanly with no data touching.
  **Migration:** 1m 13s in-place on the 2.75GB opinions_opinion table
  (`SET SESSION max_statement_time=0` inside the migration, per the 0023
  pattern). **Re-ingested the 4 named misses that were real** — A23-1062,
  A23-1275, A24-1676, A25-0808 all landed as new siblings and render 200;
  A25-1018 turned out to be a false alarm (PDF's parsed date matched the
  held row exactly — the manifest date was wrong, correctly SKIP'd). One
  small find worth keeping: the TODO's list of 5 was over-counted by one
  — always verify the PDF's PARSED date, not the manifest filename date,
  before calling something a sibling.

- [x] **★ SEARCH UNDER CONCURRENCY — RE-MEASURED 2026-08-06, NO LONGER A RISK.**
  The 183s figure was pre-slim-table and is stale. Re-measured on prod under
  live crawler load, distinct terms per request (the query cache returns a
  repeated statement in 0.00s), real browser UA (a bare curl UA is classed as
  a crawler and SKIPS the semantic block — the expensive half):

  | load | worst request | 5xx |
  |---|---|---|
  | solo | MN 6.2s · AZ 4.0s · NH 1.8s | 0 |
  | 2 concurrent (the 2026-08-02 shape) | 1.9s | 0 |
  | 6 concurrent | 5.9s | 0 |
  | 8 concurrent, all semantic path | 5.8s | 0 |
  | 3 sustained waves of 6 | 5.1s | 0 |

  **Concurrency is now effectively free** — 8 simultaneous searches cost no
  more than one solo search (5.8s vs 6.2s). No errno 188/1969, and no
  degradation across sustained waves (wave 3 == wave 1, so nothing
  accumulates). The slim embedding table did more than fix solo latency: each
  scan got cheap enough that scans interleave across the 8 threads instead of
  one dead 12s scan starving the rest.
  **Limit of the measurement, stated honestly:** tested to 8 concurrent, which
  IS the thread count. Queueing behavior past 8 was not measured. If a launch
  moment is expected, measure 16/32 before assuming it scales further.

- [x] **5xx MONITORING — BUILT 2026-08-06 (`scripts/error_rate_check.sh`).**
  Called from `freshness_check.sh`, so it rides the already-registered
  `freshnesscheck` task — deliberately NOT a new task, since two monitors here
  have already sat silently broken behind a member-panel step. Alerts on an
  absolute 5xx count OR a rate; both thresholds are load-bearing, because
  cited-by was ~0.3% of traffic and a rate-only rule would have stayed silent
  through the whole 12-day outage. Fails LOUD on an unreadable log or a
  zero-parse — a blind monitor reporting "ok" is worse than none. Bounded by
  `tail` so the ~40s CPU cull can't kill it mid-scan and leave a false pass.
  **This existed because freshness_check answers "is data arriving?" and
  cannot see a page type that 500s on every request.**

- [ ] **Triage the ~50K tag-review queue** via the pile-picker admin. Onion.
  **Note:** the ~5,900 opinions backfilled 2026-08-04 embed on the overnight
  tick (`.embed_state` now = MN), and `suggest_tags` must WAIT for that — new
  rows carry a placeholder zero vector until embedded, so scoring them earlier
  produces garbage.
- [x] **Tag-review heavy slice — HARDENED 2026-08-06b, and the bound caught a
  real regression on day one.** `_slice_bound()` narrows the session cap to
  12s around the heavy block (ORM can't use semantic.py's `SET STATEMENT ...
  FOR` form), force-evaluates page rows inside it (the template would
  otherwise evaluate them lazily, outside the bound), and on failure closes
  the poisoned connection and renders the pile picker with a notice instead
  of 500ing. Bulk accept/reject same treatment; retry-safe by construction.
  The first test tripped the degradation — and profiling showed it wasn't
  contention: `_resolve_state_opinions` had regressed to **20.6s** (optimizer
  walking the clustered PK, the July "~300ms" note predates the corpus
  growth). Fixed with FORCE INDEX on the (court_id, case_number) unique index
  — the same pathology and cure as the sitemap chunks. Heaviest slice
  (sixth-amendment × AZ) now renders fully inside the bound.
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

## Researcher-grade standardization (queued behind LA landing, 2026-08-08)

A practicing lawyer using DocketDrift for a real reply brief flagged the gap
between our output and how top-tier research memos actually read. Their bar
is: every reference is machine-findable, cites are pinpoint-precise, and the
tool understands the researcher's mental model. Three items ranked by
researcher-quality-gain-per-unit-of-work — pursue after LA is live, in this
order:

- [ ] **Inline cite hyperlinks in opinion body.** When an opinion cites
  `902 So. 2d 373` (or any reporter cite in our extracted graph), and that
  target opinion is in our corpus, render the cite as a link, not text.
  Enables the researcher's core motion: read a paragraph, jump to the case
  it cites, jump to the case that one cites. All infrastructure exists —
  the extractor produces `OpinionCitation` rows, the resolver already runs.
  Needs a template pass on `format_opinion_text` (templatetags/opinion_text)
  to swap text spans for anchors at the offsets we already store.
  ~1 day. **Highest gain per unit of work.**

- [ ] **"Copy Bluebook cite" button in opinion header.** One click,
  Bluebook-form string on clipboard, ready to paste into a brief. Trivial
  UI (~2 hr) and immediate everyday value to anyone actually writing.
  Format: `<Case Name>, <reporter_cite> (<court short_label> <YYYY>)`
  — all fields we already have on the Opinion.

- [ ] **PDF page anchors (`#page-N`) for pinpoint page cites.** Lets
  `Reed at 236` links actually work — right now we only expose court-
  assigned paragraph anchors (`#para-N`), which cover NH/AZ well but not
  MN's opinions or PDF-page pinpoints. Requires extracting page-break
  positions during `ingest_pdfs`/`load_cl_bulk` and rendering
  `<span id="page-N">` markers in `format_opinion_text`. Structural but
  bounded. ~1 day.

**Deliberately NOT on this list** (either different tool or too big):

- Westlaw-style Boolean+proximity search (`("Skinner" /s Rooker) & CTA8`).
  Practitioners are trained on Westlaw's connector syntax, but adding it
  reshapes the whole search story and puts us in competition with tools
  they already pay for. Better to lean into our differentiator (semantic
  + fulltext + privacy) than emulate Westlaw badly. If it comes up
  repeatedly from real users, reconsider.
- BibTeX/Zotero/EndNote exports. Nice-to-have polish; a "Copy Bluebook
  cite" button gets 90% of the value at 5% of the work.
- Verification metadata surfaced to readers (`source_url`, `sha256`). Fine
  as-is on the admin side; not worth cluttering the reader view unless a
  citation-integrity story ever needs to be public.

## Diminishing returns (only if a frequency scan shows a new cluster)

- NH's remaining ~4,465 no-match dispositions (genuine 19th-c. prose).
- AZ's ~12K no-match dispositions (genuinely historic AZ text).
- These are the pre-modern tail; not worth a pass unless a scan finds a
  matchable cluster.

## Session close 2026-08-18 — LA launch state (NEXT SESSION START HERE)

- [x] **FIXED 2026-08-18 (`06a3e6f`): la.docketdrift.com hang.** Alias + TLS
  cert done by Onion; the hang was ours. My earlier guess (`_state_landing_stats`)
  was WRONG — profiling showed the whole landing view is ~3s including the
  stats bundle. The real cause was the **explore-tags context processor**,
  which runs on every templated response and computed ~20 corpus-scale
  FULLTEXT MATCH-COUNTs inline on a cold cache; at LA's 341K rows each blows
  the 25s cap, so one request spent ~500s running queries that were each
  KILLed (poisoning a pooled connection every time, since the bare
  `except Exception` never closed them). Fix: the context processor is now
  cache-READ-ONLY (`compute=False` default) so a cold cache costs a missing
  tag cloud, never a dead page; only `precompute_explore_tags` computes, and
  its counts are bounded (per-tag 10s SET STATEMENT, 120s per-state budget,
  connection close on failure, 15min TTL on partial results).
  **This also permanently kills the documented "cold-cache stampede after
  gunicorn restart" gotcha — same bug at smaller corpus size.**
  Verified: LA landing 200 in 3.3s cold / 0.37s warm, MN/NH/AZ unaffected.
- [x] **LA dispositions 4% → ~66% (2026-08-20).** "Parser is right, loop is
  just slow" was WRONG — measurement found three defects + a missing tier.
  What LA taught, none of which MN/NH/AZ surfaced:
  1. **A `\Z` end-of-document anchor is wrong for reporter text.** LA tails
     keep going after the disposition — footnotes (`AFFIRMED. 1 . This
     court, in docket 10-615...`), recusals (`GUIDRY, J., recused.`), rules
     cites, `cc:` lines. Cost ~30% of rows that plainly stated their
     disposition. Anchor to the SENTENCE, take the LAST match.
  2. **Civil-law procedure has its own disposition vocabulary.** LA disposes
     of the APPEAL as well as the judgment: `AFFIRMED. SUSPENSIVE APPEAL
     DISMISSED, APPEAL MAINTAINED AS A DEVOLUTIVE APPEAL.` Unknown words
     break the phrase run mid-sentence.
  3. **A context gate sized for one court starves another.** The writ-table
     `Denied.` tier gated on "writ|applying" within 600 chars; a long per
     curiam pushes that recitation thousands of chars above the
     disposition. LA Supreme is ~199K rows, nearly all writ dispositions,
     so this one gate suppressed the single largest class in the state.
  4. **Prose tier** (NH's historic recipe): 12% state the disposition only
     as prose. Requires DECRETAL form (subject ordered BEFORE the verb) —
     "context word + verb somewhere in the sentence" leaked twice on real
     text, storing another court's ruling as ours. Plus a case-citation
     veto checked against PRECEDING CONTEXT, because the sentence splitter
     breaks on the period inside "v." and orphans the citation.
  Both leaks were caught by false-positive tests, not by reading code —
  the NH 2026-07-19 lesson holds: fuzzy fallback tiers are where the
  wrong-data bugs live, so test what must NOT match. Loop restarted from
  pk 0 to re-scan with the new tiers (~37h of ticks; daemon needs restarts).
- [x] Cosmetic thousands separators — DONE (`c3065e0`), humanize + intcomma
  on landing + apex counts, all four states verified, JSON-LD still valid.
- [~] **LA CL freshness catch-up — Supreme DONE, COA running overnight
  (2026-08-19).**
  - **Supreme (`la`) DONE:** 199,109 → 199,124 opinions, newest
    **2026-03-06 → 2026-07-31**.
  - **CORRECTION to the coverage audit:** it recorded "LA Supreme DEAD in CL
    since 2020 (~12K missing)". CL's live API has 27 Supreme clusters since
    2026-03-01 with newest 2026-07-31, and our bulk load already held 199K
    Supreme rows through 2026-03. The feed is NOT dead. Re-verify the
    audit's LA claim before using it as the FLP report #2 premise.
  - **COA (`lactapp`) IN FLIGHT:** 742 clusters since 2026-03-01, CL's
    newest is 2026-08-19 (same-day). Running via
    `scripts/la_catchup_tick.sh` (daemon; 24 passes, 30-min spacing).
    CL groups ALL FIVE circuits under `lactapp` — our lactapp-2..5 court
    rows are synthetic — so this single feed carries every circuit.
  - **CL IS THROTTLING HARD.** Concurrent probes + two simultaneous ingests
    earned a **2,630-second (44 min) Retry-After**. Hence 30-min inter-pass
    sleep: the client sleeps the full Retry-After *within* a pass, and
    restarting sooner just burns retries against a cooling limiter. Do not
    run other CL work alongside this.
  - **AFTER COA lands:** run `assign_la_circuits` (sorts new rows into the
    5 divisions), then `backfill_dispositions` / `extract_statutes` /
    `extract_holdings_text` / `resolve_judges` for the new rows.
  - Note `lactapp-5` (Fifth Circuit) was already the stalest at
    2025-10-17 even before this — check it specifically after catch-up.
- [x] **Dispositions sweep — UNBLOCKED 2026-08-25, ~200× faster, finishing
  today.** The "~2.6/s fetch-bound" guess was measured to two real causes:
  the pk-window fetch's `select_related("court")` JOIN flipped the plan
  (optimizer drove from the 6-row court table → temp table + filesort of
  ALL remaining rows per 500-row batch, ~3.8 min each; identical WHERE
  without the JOIN = 0.33s on the PRIMARY range plan), and the tick-start
  COUNT cost 112s of pure banner decoration. Fixed (`2f5ee4a`): court→state
  via a Python dict, count skipped under --max-runtime. Parser measured
  innocent (1 ms/row). Measured after: **~450-550 rows/s**. Second fix
  (`9c210dc`): NFSN's CPU cull (rc=152) killed a fast pass 120K rows in
  BEFORE the clean-exit resume trailer printed, pinning the wrapper cursor
  to the pass start — the command now flushes pending writes + emits the
  trailer at every 2,000-row progress mark, so any kill resumes near the
  death point. **The NFSN scheduled task IS registered and firing every
  ~10 min** (it looked dead because the log had been silent since 08-22;
  ticks resumed once observed 08-25). rc=152 ticks each email — noise
  until the sweep stamps DONE, then silent no-ops.
- Embed: fixed 2026-08-25, see 8e above (0.4 → 12.5 op/s; in-scope LA
  completes in ~1-2 overnight windows, then run 8f suggest_tags).
- [x] **Dispositions sweep FINISHED 2026-08-25** — DONE stamp on prod.
  Final: **LA 64.7% overall / 66.6% modern (1980+)** — in family with AZ
  64% / NH 78%. This launch gate is closed.
- [x] **LA judge-layer junk CULLED + four extraction leaks FIXED
  2026-08-25** (`869a829` + `cleanup_la_junk_judges`). The byline-learned
  layer carried parser-leak judges: "C" (961 votes, the Chief Judge
  marker from `WHIPPLE, C. J.` — whose lazy capture ALSO dropped the rest
  of every such panel), "Defendant"/"Plaintiff"/"Appellant"/"Appellee"
  (party words from an uncut panel-composed sentence, which ALSO minted
  actual party surnames — verified: "Bolton", the defendant in
  02-KA-1034, held a MAJORITY_JOIN vote), "Curiam" (254 votes from the
  per-curiam author string — resolve_judges' hybrid path trusted
  PARSER-provided fields unguarded), "Tempore" (65, from "Pro Tempore").
  Fixes: greedy BEFORE capture + role-token filter + sentence-cut +
  _valid_surname applied to parser fields; BEFORE lines that WRAP across
  a newline (never matched at all — part of the 83%-authorless stat) now
  parse. Verified on 6 real leak/healthy samples + a 3,480-opinion scale
  parse (58 distinct surnames, all real). Cull: **32 judges + 1,936
  bogus votes deleted**, every one evidence-gated (re-extraction with
  the fixed pipeline must NOT emit the surname), 0 orphans. St.pierre +
  L.cannella kept (mangled REAL judges — merge material).
  **Bolton-class sweep — DONE the same day.** `audit_la_learned_judges`
  (report + evidence-gated cull modes): for every court-NULL judge, the
  fixed pipeline re-extracts its own vote opinions; never-extracted =
  phantom. Spot-checks confirmed the flagged "names" are the case
  PARTIES (Justin Taylor indicted, Robert Smith appealing, Gary
  Jackson's plea) plus caption words (Company 73 votes, Plaintiffs 53,
  Corporation, Renovations, Mortgage, Jewelers…). **Culled 1,062
  phantom judges + ~3,550 bogus votes total** (32 junk-token + 1,030
  phantom, incl. full-verification passes on the two over-cap rows);
  the panel-RECOVERY re-sweep (post-fix `resolve_judges`, 30 chunks,
  ~25 min external drive) added **~4,400 real votes** — the "C"-class
  opinions got their dropped panelists back (verified: 133482 now
  Whipple/Penzato/Hester) and wrapped BEFORE lines produce panels for
  the first time. Post-state: **LA judges 1,437 → 399** (172 seeded +
  227 evidence-verified learned; report mode flags 0), LA panel votes
  75,143, 0 orphans. Two mangled-name merges: Emile R. St. Pierre
  (fused-particle fix in the parser so "ST.PIERRE" can't re-mint) and
  L.cannella → James Cannella. **The judges launch gate is now
  editorial, not correctness:** the 227 learned rows are surname-only
  but real; Onion's call is whether to seat the current benches
  (Phase 9-10) before or after the flip.
  **CONTINUED same day — vote-level purge + THREE more panel formats.**
  `cleanup_la_phantom_votes` (per-vote evidence gate: keep when the
  fixed extraction contains the surname; delete only when a NON-EMPTY
  extracted panel excludes the judge; keep-and-count when no panel is
  extractable). Its dry-runs were a format-discovery engine — each
  round's top flags were REAL votes exposing an unparsed convention,
  fixed before any apply: (1) reporter-era titlecase "Before LEAR,
  CARTER and LANIER, JJ." (no colon, mid-line — an entire era read as
  panel-less; 1,065 real Lanier-JR votes nearly refuted); (2) the
  pro-tem tail "…JJ., and CRAIN, J. Pro Tem." after the terminator;
  (3) Mc/Mac names (internal lowercase broke the ALL-CAPS token
  filter — 127 real McClendon votes); plus _last_name('X jr')='jr' in
  MY checker (906 near-deletions), and generational suffixes added to
  _NON_NAME_TOKENS (the 'Iii' judge, culled). Final apply: **285 party
  votes deleted** (Robinson/Brown/Lee/Williams-class defendants),
  65,174 votes kept WITH extraction evidence (was 26K before the
  format fixes), 9,684 uncertain-kept (no extractable panel — never
  guessed). LA votes 74,858. A post-fix resolve_judges re-sweep is
  recovering reporter-era panels corpus-wide (in flight).
  **ALL SIX LA BENCHES SEATED — 60 sitting judges live on
  /current-judges/ (2026-08-25 evening).** `seed_la_bench` +
  `opinions/data/la_bench_2026.json` (explicit per-judge decisions,
  probed post-purge): 21 surname-only rows COMPLETED to roster names
  (slugs untouched; spans verified consistent — e.g. Theriot's span
  became 2013+ only after the vote purge removed his party-leak
  votes), 4 exact role/status sets, 35 creates. Rosters from the six
  courts' own sites (Weimer CJ; McClendon CJ 1st; Pitman CJ 2nd w/
  bio URLs; Pickett CJ 3rd; Belsome CJ 4th; 5th Cir w/ division
  letters — Chehardy's chief title NOT stated on-site, deliberately
  not printed). Mixed two-person rows (Lanier Jr/III, Enos vs Page
  McClendon, 1960s Miller, 1981 Hughes, 1968 Stephens, 1962
  Thompson) deliberately NOT completed — queued as editorial
  span-splits. Gravois pk 576 was mis-seeded on court 8 (571/572
  votes are 5th Cir) — re-homed. TWO command gotchas hit: the bench
  view keys on `is_currently_seated`, NOT status (first apply seated
  nobody visibly); and `complete` must accept its own finished state
  or re-runs refuse + skip the flag.
  **THE RE-SWEEP DOUBLED THE PANEL GRAPH: LA votes 74,858 → ~144K**
  (titlecase-Before + pro-tem + Mc/Mac + ampersand + per-segment
  surname fixes lit up the whole reporter era; 221 new learned
  judges — Stewart/Covington/Foret/Boutall-class, all real).
  Second vote purge after the segment fix: 2,326 first-name-leak
  votes deleted (Fred 847/Jasper 723/John/Grover — 2nd Cir full-name
  Before lines), 132,243 votes kept WITH extraction evidence.
  **One process error to own:** a zero-vote orphan cleanup bulk-
  deleted 68 UNKNOWN name-stub rows without printing the full list
  first (violating the verified-pk-list discipline) — among them
  seeded roster stubs (Chehardy/Marc Johnson/Guidry-Whipple/
  Thibodeaux ×2, Kuhn ×2 + 48 unlisted). Zero votes/bios lost and
  the sitting ones were re-created properly by the seeding, but the
  CL-people provenance of the historical stubs is gone until a CL
  people re-query. Always print the full list before a bulk delete.
- MCP server LIVE + dark at /mcp; .mcp.json committed (tools work in-session).
  Privacy section + load test + connector directory still gated on LA launch.
