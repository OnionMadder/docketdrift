# DocketDrift — notes for Claude sessions

Survival kit for any Claude session working on this repo. Read once,
re-read whenever a recurring gotcha bites. The goal of this document is
to make the next session productive within the first 5 minutes.

## Working tree state 2026-08-02 — READ THIS FIRST

The tree is **clean**; main == origin/main. Backlog lives in `docs/TODO.md`
(the authoritative to-do; keep it current). **`docs/TODO.md` outranks the
"Open work, ranked" section far down this file** — that section is a frozen
2026-06-12 snapshot kept for its rationale, and several of its "open" items
have shipped. Trust TODO.md on priority; trust this file on gotchas.

**MN 2020–2022 IS FIXED (2026-08-03): 0 → 3,102 opinions.** 2020=1,040,
2021=1,092, 2022=970, read directly from the mn.gov State Law Library archive.
MN corpus 60,457 → **63,559**. Derived layers ran: 18,254 statute-cite rows,
1,958 holdings, 4,315 panel votes. **2017–2019 and 2023 are STILL THIN** — see
the backfill recipe + the case-number blocker in the 2026-08-03 block below.
Residual, disclosed on `/about/`: COA ~83%, Supreme ~53% (the archive carries
no Supreme *orders*), and these opinions have **no reporter cites and no
citation-graph edges** (both derive from CL bulk data, which has nothing for
years CL lacks).

**THE CAUSE WAS UPSTREAM, NOT US — corrected 2026-08-03. Do not re-derive this
the hard way.** Earlier notes (including this block, as first written) blamed
the `/search/` vs `/clusters/` ingest defect. **That is wrong.** Measured three
independent ways on 2026-08-03:

1. **CL's bulk export (2026-03-31) has the same hole.** Filtering
   `mn-subset/opinion-clusters.csv` by year: 2016=1,451 → 2017=458 → 2018=213
   (**all Published, zero Unpublished**) → 2019=185 → **2020/2021/2022 = 0** →
   2023=115 → 2024=556.
2. **Prod matches the bulk export almost 1:1** for every year — so we loaded
   essentially everything CL had. Nothing was dropped on our side.
3. **CL's LIVE API still returns zero.** `/clusters/?docket__court=minnctapp`
   for 2021 → **count=0**; `minn` (Supreme) 2021 → **count=0**; control query
   `minnctapp` 2016 → **count=1,231**. The query shape is fine; the data isn't
   there.

**Both courts are affected**, including MN Supreme *published* opinions — which
the `/search/` defect could never explain (that defect cost us nonprecedential
opinions only). This is a CourtListener coverage hole for Minnesota, most
likely a juriscraper that broke around the 2020 mn.gov redesign and was
backfilled only from 2023 forward.

**Consequences — read before planning any work here:**
- **No CL path fixes this.** Not `load_cl_bulk`, not `ingest_court`, not a
  newer bulk snapshot. Don't burn hours re-running loaders; the rows do not
  exist upstream. (Worth telling Free Law Project — it is a real, specific,
  reproducible bug report and a better first contact than anything else we
  have.)
- **The opinions themselves are fine and freely available.** Verified live:
  `mn.gov/law-library-stat/archive/ctapun/2021/OPa210414-112221.pdf` → HTTP 200
  application/pdf, and our MN parser reads it correctly (A21-0414, 2021-11-22,
  Affirmed, author + panel + 4 statutes, precedential=False). The PDF host has
  **no bot wall**.
- **Only the LISTING is walled.** Enumeration is the entire remaining problem:
  the year directory (`/archive/ctapun/2021/`) returns a Radware 302, the old
  `a<NNNNNN>.pdf` URL scheme is dead (404), and the live scheme
  `OP<case>-<mmddyy>.pdf` requires the filing date, so URLs can't be guessed
  from a case number alone. (Filing dates are always **Mondays** — a useful
  constraint, but 1,800 cases × 52 Mondays is still far too many probes.)
- **The existing scraper cannot do this job.** `scrape_mn_coa.py` walks the
  newest-first archive list and stops at pager page 10 (~100 opinions); it has
  `--since` but no upper bound, so it cannot reach a historical window. The
  backfill needs a **date-windowed search** build — see the 2026-08-03 block.

Measured prod, MN by year and court (2026-08-03):

| year | COA | Supreme |   | year | COA | Supreme |
|---|---|---|---|---|---|---|
| 2016 | 1,223 | 192 | | 2021 | **0** | **0** |
| 2017 | 313 | 125 | | 2022 | **0** | **0** |
| 2018 | 80 | 128 | | 2023 | 97 | 18 |
| 2019 | 74 | 102 | | 2024 | 463 | 87 |
| 2020 | **0** | **0** | | 2025 | 148 | 90 |

Until the backfill runs, the public copy discloses the gap **and now names the
upstream cause correctly** (`/about/`). Do not remove that disclosure.

The old parked branch
**`parked/holdings-admin-and-citation-clustering`** still exists but is now
mostly historical:

- **Citation clustering** was CHERRY-PICKED to main + SHIPPED NH-first this
  session (so the branch's clustering commit is redundant with main). It works
  but is data-starved — see the 2026-07-24→25 session block below.
- **Holdings review admin** was cherry-picked, de-LLM'd, then **REVERTED** (it
  scans the 2.75GB table past the 25s cap → needs an index migration first).
  Recoverable from reverted commits `7498db0`+`324f9c5`.
- The **LLM `extract_holdings` command was DROPPED from main** — it lives only
  on the branch. The product is extractive; ML stays in two places.

The `about.html`/`how_we_differ.html` landmine (LLM "summarized holding" claims
in indexed JSON-LD) was reverted — extractive holdings = "no generated text",
the strong original posture, is correct. Migrations 0026 (holdings) + 0027
(clustering) are on main and applied on prod.

## Latest session (2026-08-10 → 16) — page anchors; LA Phase 8; embed 100×; judge heat + real dissents

Multi-day rolling session. Everything committed + deployed + live-verified
unless noted. Chronology compressed; lessons inline.

**PDF page anchors SHIPPED (`#page-N` deep links), AZ-first.**
`format_opinion_text` splits raw_text on pypdf's `\f` form-feed BEFORE the
blank-line chunk pass and emits an `<hr>`-style divider with `id="page-N"`
(mirrors `#para-N`; `.op-page-break`/`.op-page-anchor` CSS). No per-state
gating — absent `\f`, the split is a no-op. Coverage measured (modern 1980+):
AZ 50%, NH 31% (2020s = 98%), MN 14%, LA 10%. **The gap is CL's upstream data,
not ours** — verified three ways: CL's bulk CSV carries `\f` (5 in the probe
row), MariaDB round-trips 0x0C perfectly, `load_cl_bulk._best_text` doesn't
strip. Pre-2010 CL text comes from Harvard/Columbia scans with no page
structure; no loader fix recovers what upstream doesn't have. A
`backfill_pages_from_cl` command + wrapper exist (fetch cluster →
sub_opinions → plain_text with `\f`) but were STOPPED as not worth CL's rate
limits (~600 truly-fixable NH rows at <60/hr). **CL API GOTCHA for any future
work: `Opinion.courtlistener_id` holds a CLUSTER id, not an opinion id — the
website URL `/opinion/<id>/` is the cluster view. `fetch_opinion(cluster_id)`
returns a random federal opinion. Walk `clusters/<id>/` → `sub_opinions` →
opinion.** Also fixed while investigating: `Opinion.extract_text_from_pdf()`
and `ingest_pdfs._extract_pdf_text()` were joining pypdf pages with plain
newlines — both now `"\n\f\n".join` so admin uploads + scraper ingests get
page anchors automatically.

**`_match_opinions` regression: the 'No. X' sibling widening (8949e48) used
`case_number__in=[...]`, which flipped the optimizer off the
`(court_id, case_number)` composite index → 25s KILL on EVERY AZ/MN opinion
page.** Fixed with sequential per-variant equality lookups (~1-2ms each, ≤4
keys). The IN-list pathology is the same "one non-covered column beside a
court_id filter" gotcha — now seen in a WHERE clause too.

**embed_opinions was 100× slower than it should be — ORDER BY id.** Timing
probe: 90s of each 99s batch cycle was the fetch; EXPLAIN showed `ORDER BY id
LIMIT 30` walking PRIMARY from id=1 through ~130K already-embedded rows
instead of using `(embedding_pending, court_id)`. Dropped the ORDER BY
(ordering is irrelevant — the flag flip makes the next fetch skip done rows):
fetch 96,060ms → 535ms cold / 2ms warm. LA embed now ~3.2 op/s (Voyage's ~8s
per 90K-token batch is the ceiling), finishing in days not weeks. **Real
measured cost: $0.15 per 500 LA opinions (~2,485 tok/op) → ~$100 full LA**;
`--since` flag added for era-scoping the spend. `.embed_state=LA`, overnight
window, in flight (~19K done at last check).

**LA Phase 8a–d COMPLETE** (8e embed in flight, 8f tags blocked on it):
statutes **252,375** rows / citations **1,543,411 edges (56% of opinions)** /
judges **1,437** (172 seeded + ~1,265 byline-learned UNKNOWN awaiting
editorial) with **74,222 panel votes** / holdings **34,624**. Hard-won loop
lessons, each measured after days of silent zero progress:
- **A wrapper without `--min-id` cannot advance past legitimately-empty
  rows.** The SQL done-exclusion only marks opinions that GOT rows; LA's
  dense per-curiam tail (200K+ opinions with no statute/holding language)
  was re-scanned from pk=0 every tick until the cull hit. `extract_statutes`,
  `extract_holdings_text` now take `--min-id`/`--max-runtime` and print the
  shared `resume with:  --min-id N` trailer (DOUBLE space — the wrappers
  grep that exact string; a single-space print cost a day of min-id=0 loops).
- **rc=152 = SIGXCPU** (CPU cull, ~40s under contention): the command must
  self-exit via `--max-runtime`, or it dies BEFORE printing its resume line
  and the next tick replays the same range forever. **rc=137 = SIGKILL**
  (memory cull): `qs.iterator()` on Django's MySQL backend buffers the WHOLE
  result client-side — 341K × 11KB = 3.7GB. pk-windowed short queries
  (`pk__gt=last ORDER BY pk LIMIT 500`), never iterator, for corpus scans.
- **`nohup … & disown` does NOT survive ssh disconnect on NFSN; FreeBSD
  `daemon -p pidfile cmd` does** (and even those get culled eventually —
  check `ps` before trusting any loop, and prefer NFSN scheduled tasks for
  anything that must outlive a session).

**Judge co-panelist heat SHIPPED + the data to light it.** Dossier cohort now
shows aligned/partial/split chips per co-panelist (green/cyan/pink from the
`--dd-*` semantic key; `_cohort_with_heat` in views.py). First version
2013'd on high-vote judges (2,622-id IN + JOIN past the 25s cap) → 3-query
design: top-N judges first, then primary votes, then top-N votes on shared
opinions. THEN the chips exposed a data hole: **every state read ~100%
aligned because bulk-loaded panel votes were all MAJORITY_JOIN and dissent
extraction only understood NH's footer.** Fixed per-state, measured-first
(frequency-ranked the real conventions before writing regexes):
- **NH:** footer re-sweep + `(dis)?senting` variants → 0 → **299 dissents**,
  +240 author upgrades.
- **MN caption lines** (`Dissenting, Harris, Judge` / multi-dissenter Supreme
  lists / `Concurring in part, dissenting in part, X` / `Concurring
  specially`): 0 → **294 dissents + 150 concurrences**, and the caption
  extractor recovered **+14K total votes** (authors 5.6K → 10.1K) — MN was
  broadly under-extracted, not just for dissents.
- **AZ prose** (`Judge Eckerstrom dissented`, `concurred in part and
  dissented in part`, `specially concurred`, paren-guard + SCOTUS stoplist
  intact): 2 → **131 dissents + 43 concurrences**.
- New **Pass 4** in resolve_judges mints CONCURRENCE_AUTHOR + upgrades
  MAJORITY_JOIN in place (same shape as the dissent pass); `GenericByline`
  gained `concurrer_last`. Sanity check that landed: AZ's most-split dossier
  is **Bolick** (11 aligned/5 partial/10 split) — the court's famous
  separate-opinion writer. MN's is Gildea. Compare-judges inherits it all.

**Ops gotchas added this session:**
- **Cold-cache stampede after gunicorn restart:** with the explore-tags
  context-processor cache expired, crawler traffic re-runs ~20 corpus-scale
  MATCH COUNTs per templated render through the 8MB buffer pool — the site
  reads "down" for ~10 min while healthz (no template) stays instant. Run
  `precompute_explore_tags` after restarts; diagnose with the in-process
  Django test Client render (4.3s there vs 30s+ timeouts outside = queueing,
  not code).
- A backgrounded command piping to `| tail` holds its output until process
  exit — an ssh drop loses it silently. Log to a file on the remote side.

## Prior session (2026-08-07 → 08) — search cliff gone; cited-by fix; dup merge; MN 2026; AZ judges rebuilt end-to-end; NH re-pitch; IA published

Very long multi-thread day. Everything below is committed + deployed + verified
unless noted. Details live in the commit messages and `docs/TODO.md`.

**Search concurrency — the #1 launch risk is GONE (re-measured 2026-08-07).**
The 2026-08-02 "183s under 2 concurrent" cliff no longer reproduces. Measured
against prod under live crawler load: 8 concurrent searches (all on the
expensive semantic path) top out at **5.8s**, same as one solo search; 3
sustained waves of 6 showed zero degradation; no errno 188/1969, no poison
cascade. The slim-embedding-table fix (2026-08-05b) did it — cheap-enough
cosine scans now interleave across the 8 threads instead of one dead 12s scan
starving the rest. Tested to 8 (= thread count); past that requests queue,
unmeasured. **Downgrade this from "#1 risk" to closed.** Method note: the
capped path (200+ matches → no semantic/snippets) is NOT a failure; label
detection by exact copy, not a guessed string.

**cited-by hard-500'd 1,844 pages for 12 days — fixed.** `opinion_cited_by`
used a bare `.get(case_number=...)`; a docket follows a case COA→Supreme, so
1,844 shared dockets raised `MultipleObjectsReturned` → instant 500 (27ms, not
load). It was **49% of ALL 500s** and burning GPTBot/Googlebot crawl budget on
exactly the citation-graph pages that are the product's differentiator. Fix:
the existing `_pick_opinion`/`_match_opinions` (never wired into this view) +
`?court=` + `NoJoinCountPaginator` + `.defer(raw_text, html_content)` (the
page dragged 1.4MB it never renders → the intermittent errno-2013 half). Found
only because a crawler tripped it during the search probe — **nothing watches
the 5xx rate**; a whole page-type 500'd through the Thursday audit unnoticed.

**merge_duplicate_opinions — 605 duplicate pairs merged.** The
two-spellings-of-one-docket rows `normalize_case_numbers` deliberately skipped.
New idempotent command (dry-run default, 3 gates: same court, same date,
token-containment title match; every refusal reported). **4,465+ inbound
citation edges re-pointed** (a bare delete would have cascaded them away). The
508 different-date pairs (opinion + amended/order) are never eligible. Verified
after: corpus −605, **0 orphaned FKs across 7 tables, 0 self-edges, 0 dup
keys.** GOTCHA baked in: the slim embedding table has no secondary index, so a
DELETE `WHERE opinion_id=X` there LOCKS all 128K rows (errno 1206) — address
its full clustered PK `(court_id, release_date, opinion_id)`. Also: shared-DB
2013 drops mid-chunk are normal; per-pair transactions make a died chunk
cleanly resumable.

**cl-`<id>` case numbers — RESOLVED, and the plan on file was WRONG.** The
TODO's `fetch_docket()` repair would have recovered ZERO: joining all 14,436
rows against the LOCAL CL bulk dump (offline, no API) showed **CL's own dockets
table is EMPTY for 14,416 of them** (live-API probe confirmed). AZ's 24 modern
rows had the docket in their OWN caption text (extracted, court-aligned,
collision-checked). MN/NH's are 1800s–1930s reporter OCR with no docket
anywhere — but **MN 100% / NH 99% carry a `reporter_cite`**, the identifier a
lawyer pastes for that era, so no reachability gap. Lesson: measure against the
bulk dump BEFORE planning any CL API work.

**tag-review hardened + a silent 20.6s regression caught.** `_slice_bound()`
(12s cap + degrade-to-picker, mirrors `semantic.py`) — and its first test on a
QUIET DB revealed `_resolve_state_opinions` had regressed to **20.6s**: the
optimizer had flipped to a clustered-PK walk as the corpus grew (the July
"~300ms" note was true then). Fixed with FORCE INDEX on `(court_id,
case_number)` + per-court equality. Same pathology bit the sitemap chunks the
day before — see the "One non-covered column beside a court_id filter" gotcha.

**MN early-2026 backfill — 519 opinions, end to end.** Jan–Jul 2026 (the last
thin span; weekly scraper only reaches ~mid-July). 747 swept → 746 fetched (0
fail) → 519 created → statutes/holdings/judges/citations (6,451 edges) →
reconciled. Finding worth keeping: **same-court second decisions on one docket
are silently DROPPED at ingest** (the `(court, case_number)` key can't hold
opinion + amended-opinion; the COA/Supreme pair only coexists because the court
differs). 5 named 2026 docs affected; the weekly scraper shares the blind spot.
New Tier-4 item — needs a schema call, not a `--update` bolt-on.

**AZ judge roster REBUILT end to end — 247 → 194, then the current bench
seated.** The "AZ has 2x the judges" mystery was three extraction defects:
- **`cleanup_az_judges`** (one-shot, verified pk lists): 11 junk tokens
  (And/Appel/Opinion/State/M…), 24 cross-court citation leaks (Kozinski/9th,
  Dietzen/MN, Titone/NY, Sealia=Scalia…), 10 dist-1 OCR merges → 247→202.
- **Tier-2 merges** (202→194): Flórez accent cluster, **Prade→Arthur Thornton
  LaPrade** (125 votes onto the real 1940s Supreme justice), Pinney territorial
  OCR cluster, Ppielps→Phelps, Arabian (a *California* justice) cull.
- **Phantom-author cull**: the July cull left Connor/Souter/Burger recorded as
  the AUTHOR of ~12 AZ opinions they're merely cited in; + DeConcini merge
  ("Concini" split → the real Evo Anton DeConcini row).
- **ROOT FIX in `resolve_judges`** so leaks/junk don't recur: `_inside_open_paren`
  (a `(Name, J., concurring)` inside parens is a CITATION, not a panel signoff —
  name-agnostic, catches circuit/state judges the SCOTUS-only stoplist can't;
  KEEPS the bare-signoff 19th-c. Territorial justices) + `_valid_surname`/
  `_NON_NAME_TOKENS`. Integration-tested: Tjoflat/Dietzen opinions now extract
  nothing; real Vásquez byline + territorial Tweed signoff still extract.
- **Sitting bench fully seated (35 across 3 courts).** Supreme corrected 8→7
  (Lopez seated as Vice Chief, 2 misflagged COA judges unseated); COA split into
  **Division One (19) / Division Two (9)** and both benches seated with full
  names/roles/division/bio links. Sourced from the courts' OWN rosters
  (azcourts.gov/MeettheJustices, coa1.azcourts.gov, appeals2.az.gov) — **the
  in-app browser gets through the Akamai wall that 403s server fetches**; the
  static image ASSETS are fetchable server-side even when the page isn't.
- New roles `VICE_CHIEF_JUSTICE` + `VICE_CHIEF_JUDGE`; new `Judge.cl_absent`
  (a terminal "confirmed real, not in CL" state so state judges CL will never
  carry stop reading as unresolved); collapsible admin filter sidebar; the
  judge card now renders `bio_url` as an "Official bio ↗" link (was in the DB,
  never output).

**`Court.division` added; AZ COA split into two real courts.** Court identity is
now `(state, level, division)` — empty for single-court levels, "1"/"2" for AZ's
divisions (ready for CA's 6 / TX's 14). `assign_az_divisions` (idempotent, run
after AZ ingests) reassigned by docket prefix: **Div One 18,593 / Div Two
5,717**, 37 malformed left on Div 1 + reported. Updated the slim embedding
table's `court_id` in lockstep (part of its clustered key). Opinions stay ONE
unified searchable corpus — court is a facet, not a fork (agreed with Onion).

**NH landing re-pitched for the reader, not the analyst.** Shared
`state_landing.html` (all 3 states, state-agnostic): hero leads "Free,
searchable {state} case law — no account, no paywall"; a value trio
(Free & open / Private by design / The court's own words) surfaces the
free+privacy+no-generation posture that was buried in nav; "What you can do
here" names the practitioner power-features (paste-a-cite, treatment, verbatim
holdings, dossiers) in plain language; front-page jargon (MariaDB/voyage)
dropped to /about/. Built to present to NH Legal Aid, kept broad.

**MN opinion bundle PUBLISHED to Internet Archive** (10,137 opinions 2017–2026,
`archive.org/details/minnesota-appellate-opinions-2017-2026`). Lesson learned
the hard way: **10K individual files via `ia upload` is ~9.6h and fragile** —
each file is a sequential ~3.6s API round-trip, and `--checksum` can't skip on a
fresh item (IA hasn't derived checksums yet), so a resume re-uploads from the
top. Switched to a single 1.63GB ZIP (internal paths match `manifest.csv`) +
loose manifest/README — minutes, robust. IA had a 503 outage mid-session and
rate-limits deletes (`bucket_tasks_queued`), so a patient background pass clears
the leftover loose PDFs over hours. Verify a big IA upload by the download URL's
Content-Length, not `ia list` (metadata lags commit by minutes). Item is live +
verified: `archive.org/details/minnesota-appellate-opinions-2017-2026` (the zip
is the complete, correct dataset regardless of how many loose leftovers remain).

**Louisiana is scoped — `docs/LA_BUILD_LIST.md` (new 2026-08-08).** The full
LA-specific overlay on the 12-phase rollout: two-source Supreme ingest
(lasc.gov for the CL-dead 2020+ gap), five COA circuits via `Court.division`,
civil-law citation grammar. ~5–7 days, the lasc.gov backfill the swing.
**NEXT SESSION = LA Phase 0 recon** (the queued work): probe lasc.gov
(open vs Akamai-walled — decides whether the Supreme backfill is unattended or
an MN-style CAPTCHA sweep) and find the circuit-assignment key (how a `lactapp`
bulk row says which of the 5 circuits decided it). Those two answers size the
whole build, and the recon doubles as FLP report #2. Also worth noting: MN
2024–2025 turned out ALREADY DONE (live check: 1,161 / 1,130, matching the
archive-sweep "done" figures) — a stale TODO detail item had implied otherwise;
measure the gap before launching any attended sweep.

## Latest session (2026-08-06) — Thursday audit; weekly scraper rebuilt; AZ graph; error reports; coverage audit; design sweep

Long day, many threads; details live in the commit messages and the sections
referenced below. What shipped and what it changed:

- **Thursday audit: GREEN everywhere except one finding.** All pages 200 on
  all subdomains; search holding (MN 1.3s / AZ 1.4s solo, 3-way concurrent
  1.1-2.4s — the slim-table fix is stable); overnight embed cleared 5,693 ->
  9; slim table in EXACT sync with embedded count; zero future dates, zero
  duplicate edges, zero slim orphans. The finding: **Monday's MN weekly
  scrape failed silently** (Windows Last Result 1 — it collided with the
  attended backfill session holding the same Chrome profile) and MN sat 10
  days stale with nothing alerting.
- **Weekly scraper rebuilt in manifest mode** (`run_mn_weekly.ps1`): the
  in-page PDF fetch ALSO broke this day ("TypeError: Failed to fetch",
  40/40), so the weekly now uses the backfill split — browser only LISTS,
  NFSN downloads — and forward-fills the SUPREME court too (the old weekly
  was COA-only; Supreme arrived solely via the degraded CL cron). Catch-up
  ran: 40 opinions, MN newest 2026-08-05. **Success beacons added**: both
  weekly wrappers stamp `.scrape_{mn,nh}_last` on NFSN after every
  successful run (including the legitimate "nothing new" exit) and
  `freshness_check.sh` alerts when a beacon is missing or >9 days old. A
  dead weekly scraper now surfaces in one email cycle, not at the 45-day
  corpus threshold. Beacons are seeded only AFTER verifying a real run — a
  beacon stamped on faith recreates the false-health signal it exists to
  kill.
- **AZ citation sweep ran: 313,120 extracted edges** (198,709 quotes, 1,814
  treatments) — the parity gap closed; all three states now carry the full
  extracted graph. Totals: **1,462,119 edges** (605,353 bulk + 856,766
  extracted = MN 468,595 / AZ 313,120 / NH 75,051), 5,188 non-default
  treatments.
- **NH is slowest BECAUSE smallest** — measured, explained, deliberately
  accepted; see its own section above. Do not "fix" via the candidate cap.
- **`/report-error/` shipped and verified end-to-end** — nav link on every
  page (?page= prefill, path only), plain-Form emailed via sendmail,
  persisted NOWHERE, honeypot, send-failures shown never swallowed. The
  debugging found a header-injection hole (closed via EmailMessage) and
  three mail traps now recorded in the "Sending email from NFSN" gotcha.
  En route: `hello@` forwarding CONFIRMED working (NFSN Hybrid Forwarding,
  hello -> kellye.sundar@gmail.com, catch-all -> onionmadder@gmail.com).
- **Every-state CL coverage audit** (`docs/cl_coverage_audit/`): the MN
  diagnostic run across all state appellate courts. **17 courts show
  MN-shaped degradation (~60-80K opinions missing 2023-2025)**; worst is
  Louisiana Supreme — dead since 2020, ~12K missing, the largest
  single-court hole in the country — while LA's COA feed is intact. That
  split shapes the LA rollout (COA from CL bulk, Supreme direct from
  lasc.gov) and makes LA the natural FLP report #2. Caveats in FINDINGS.md
  (candidates not verdicts; the flagged list is a FLOOR — pre-2019 breaks
  like MN itself are invisible to the 2012-2019 baseline).
- **FLP thread advanced**: their support reply ("let's talk on GitHub")
  bridged onto issue #1115 under Onion's account; the offer now reads
  2017-2026, ~10,100 opinions; handover bundle staged at
  `/home/private/handover/`. Waiting on them; do not nudge before ~2 weeks.
- **Funding surface built**: Ko-fi cover (brand banner, 1200x400), costed
  "Launch Louisiana" $250 goal, page copy tightened to house voice; X
  profile refreshed (bio/banner); support page now names Louisiana with the
  itemized goal and had two stale cost numbers corrected to measured ones.
  `DONATE_URL` restored in prod `.env` (it silently vanishes when unset —
  now in `.env.example`).
- **Onioncore design sweep: 30 rules re-pointed.** One grammar site-wide:
  primary cards keep corner ticks; every other accent is a side strip
  fading top->bottom, painted the same way the ticks are, `--strip` as the
  per-element knob. Boxed banners quieted. The semantic color key
  (disposition/treatment/review edges, vote chips) deliberately untouched —
  those edges are data, not chrome. Layout shifted zero pixels.

**Methodology lessons this day (all bit me):** a task can fail while its
panel shows a healthy "Last Run" (run the script, don't read the status);
git-bash heredoc surgery mangles backslash escapes — use the Edit tool for
code containing them (three strikes now); Windows curl mangles non-ASCII
form input — test unicode paths from the FreeBSD side; and the fix for a
wrongly diagnosed system is often worse than the disease (the From-header
revert).

## Prior session (2026-08-05b) — search fixed: slim embedding table; query-log privacy hole closed

The "search concurrency problem" was misdiagnosed. Profiling per phase showed
the MN/AZ cosine scans had crossed their feasibility point: ~2,400 rows/s on
the fat table means MN (69K embedded rows) needs ~29s against the 12s bound —
**killed every time, every search burning 12 thread-seconds for zero results**.
At workers=1 × 8 threads that is the 183s cliff from the 2026-08-02 audit:
capacity ~0.7 searches/s, everything queuing behind dead scans. The cost is
fat-table IO, not vector math — scanning `embedding` drags the 2.75GB
clustered rows through the 8MB pool. NH's own history proves it: 215ms in
June, 8.5s now; its corpus grew 6 rows while the shared table grew ~10K.
A 15-year window did NOT help on the fat table (optimizer still walks it).

**Fix shipped: `opinions_opinionembedding`** (migration 0031) — slim table,
clustered PK (court_id, release_date, opinion_id), embedding VECTOR, **no
secondary indexes, deliberately NO vector index** (the 2026-06 HNSW attempts
failed at index BUILD; this table makes the O(N) scan cheap instead). Only
embedded rows enter it, so scans need no embedding_pending predicate. Raw SQL,
no Django model (same precedent as Opinion.embedding). Backfilled 122,417 rows
in ONE pass (`sync_embedding_table`, idempotent, --prune anti-joins stale
rows). `embed_opinions` dual-writes via INSERT..SELECT from the fat row it
just updated, so court/date can't drift. Measured cold, 10-yr window: **MN
2.2s / AZ 2.8s / NH 0.8s** (vs killed / killed / 7.6s full-fat). The view's
DEFAULT_SEARCH_YEARS=10 keeps default searches on that path; `years=all`
full-scans complete on NH and degrade to [] at the bound on MN/AZ — behavior
falls out of the bound, no per-state gating. **Live after deploy: MN search
13.3s → 1.7s; three concurrent searches 1.3/1.3/2.3s** (was 183s for two).
The MN semantic block renders for the first time since the corpus outgrew the
fat scan. If cosine latency creeps up again as the corpus grows, widen nothing
— check rows/s against the bound and shrink the window or revisit infra.

**Privacy hole found en route and closed: `QueryEmbedding` stored every search
query VERBATIM** (PK on query text, hit_count, last_used_at — 36 rows on prod)
while the Privacy page said "We don't log search queries." By the project's
own subpoena test that table must not exist. Migration 0030 drops it (rows
destroyed); the cache is now a process-local dict in semantic.py (workers=1
makes it effective; misses cost ~$0.000001; crawlers never reach the path).
**Lesson: both of the last two integrity failures (this and the eyecite claim)
were found as side-effects of unrelated work, not by audit.** When touching
any cache, ask what it persists and whether the Privacy page still tells the
truth.

Probe-methodology traps that burned time this session: `curl` without a
browser UA is treated as a crawler and silently SKIPS semantic search (test
with a real UA); MariaDB's query cache returns identical repeated statements
in 0.00s (vary the vector or treat rep-2 timings as meaningless); and piping
a probe through `grep` filters the WARNING lines out of the saved output —
capture full output, filter at read time.

### NH search is slowest BECAUSE it's smallest (2026-08-06) — accepted, not a bug

Thursday audit flagged NH as the slowest search (4.8s) despite the smallest
corpus. Per-phase profile: it is ENTIRELY the FULLTEXT candidate fetch on
common terms ("negligence" fulltext=5.16s; everything else <0.8s). Mechanism:
the fulltext index spans the whole shared table; the candidate query matches
corpus-wide and discards non-NH rows, each discard costing a clustered-row
fetch. NH is 16% of the corpus, so filling 200 NH candidates wades ~4x more
postings than MN at 54% — **the smallest state pays the most, and its cost
grows when OTHER states' corpora grow** (this week's +9K MN rows made NH
slower). Specific terms are unaffected ("zoning variance" fulltext=0.88s,
total ~1.5s).

**Deliberately accepted.** Lowering FULLTEXT_CANDIDATE_CAP would speed the
over-broad dead-end page but reclassify every 51-200-match search as capped,
stripping semantic + snippets from exactly the most useful mid-size searches.
The 5s case is bounded (12s), non-poisoning, and only hits ultra-common terms
that already dead-end at "narrow your search." If it ever matters: per-state
caps proportional to corpus share are the dial. Do not "fix" this by lowering
the global cap without weighing the 51-200 band.

## Prior session (2026-08-04) — MN citation extractor; AZ blocked on parallel cites

**MN now has a text-extracted citation graph: 353,992 edges**, alongside (not
replacing) CourtListener's 605,353 bulk edges. 272,158 carry a context quote.
**All 3,102 backfilled 2020–2022 opinions have graphs** (37,298 edges) — the
one layer they could never inherit from CL.

**Treatment classification finally produces signal.** Corpus-wide it had
produced ZERO non-default values in every state; now: 1,211 Distinguished, 341
Followed, 222 Explained, 219 Criticized, **165 Overruled**. "Has this been
overruled" is the question a lawyer most needs answered, and until today the
site answered it never.

- **`opinions/parsing/citations_mn.py`** — scope chosen by measuring
  resolvability, not frequency, on a 900-opinion sample: N.W.2d 94%, N.W. 1st
  88%, docket `A##-####` 87% → extract; **`Minn.` official 3% → EXCLUDED**
  despite being the 2nd most common format (our `reporter_cite` holds the
  regional cite, so official cites resolve nowhere).
- **Docket citations are MN-only and load-bearing** — a docket is the only key
  reaching an opinion with no reporter cite (every unpublished one, plus the
  whole backfill).
- **Three false-positive classes, all found by READING SAMPLE OUTPUT, not by
  tests:** spaced `131 N. W. 2d 855` parsed as first-series vol 131 page 2 and
  resolved to the WRONG case; self-citation via the caption (the guard failed
  for the ~15K malformed dockets — `a230373` uppercases to `A230373`, never
  equal to the emitted `A23-0373`) — **my leak COUNTER had the same bug and
  reported zero**; and consolidated appeals listing companion dockets in the
  caption (`A23-0373, A23-0621`), which would draw edges between documents that
  are one proceeding. A docket now counts only when followed by a Minnesota
  court parenthetical.
- **`extract_citations` had two NH-era assumptions** that made it unusable for
  MN: it scanned only opinions WITH a reporter_cite (skipping every unpublished
  opinion and the entire backfill), and resolved only by reporter_cite. Now
  scans everything and resolves by docket too. **Ambiguous dockets are dropped,
  never guessed** (676 in MN — a docket follows a case through review).
- **Migration 0028 adds `OpinionCitation.source`** (`bulk` vs `extracted`).
  Both are kept: CL's map resolves against THEIR full corpus so it reaches
  cases we don't hold; ours carries quotes + treatment and covers what CL
  lacks. `extract_citations` now deletes only its OWN rows — the old
  delete-everything would have destroyed CL's 335,998 MN edges on the first
  full sweep. `opinion_detail` dedupes per relationship, preferring extracted.
  Migration took 28s (no index on the column, so no rebuild).

### eyecite: INSTALLS FINE on FreeBSD, but MEASURED NOT WORTH DEPLOYING (2026-08-04)

Settled with numbers so nobody reopens it every few months. Two questions, both
answered.

**1. Does it install on NFSN's FreeBSD? YES.** The requirements.txt warning
("FREEBSD RISK … do NOT pip-install until confirmed") is now resolved: in a
throwaway `/tmp/eyetest` venv, **all four C-extension deps built from source**
— `fast-diff-match-patch`, `lxml`, `pyahocorasick`, `regex` — giving
`eyecite 2.7.8` + `reporters-db` + `courts-db`. It also IMPORTS and tokenizes
correctly, which is the check numpy fails (numpy's wheels build and then die on
a missing `cblas_sdot` at import). So the install risk is closed. **Do not
re-test this.**

**2. Is it worth wiring? NO, not for graph coverage.** Bake-off on 300 real MN
opinions, both extractors resolving against the SAME key set (reporter_cite ∪
ParallelCite ∪ docket map), counting RESOLVED TARGETS (what becomes an edge),
not citation strings:

| | resolved targets |
|---|---|
| ours (`citations_mn`) | **1,661** |
| eyecite | 1,574 |
| shared | 1,559 |
| **ours only** (eyecite misses) | **102** |
| **eyecite only** (we miss) | **15** |
| eyecite hits re-referencing a target we already have | **1,898** |

- **eyecite would add ~1%** of targets (~3K corpus-wide) and a naive SWAP would
  **lose ~21K** edges, because **eyecite does not model DOCKET citations** —
  it's a reporter tokenizer, and `No. A19-1234 (Minn. App. 2020)` isn't a
  reporter cite. Dockets are the only key reaching unpublished opinions and the
  entire 2020–2022 backfill. **So it could only ever be a merge, never a swap.**
- **Its 1,898 extra hits are short forms** (`425 N.W.2d at 582`, `id.`,
  `supra`) pointing at cases ALREADY cited in full. Under one-edge-per-resolved-
  target dedup those produce **no new edges**. Do not quote "eyecite finds 2×
  the citations" as if it meant 2× the graph — it doesn't.

**Caveat, stated because it cuts against the recommendation:** the comparison
is BIASED AGAINST eyecite. It emits reporters with internal spacing (`297 N. W.
710`, reporter `N. W.`) and the bake-off didn't normalize that, so some of the
102 "ours only" is a normalization gap on OUR side, not an eyecite miss. True
"eyecite only" is higher than 15. The structural finding survives correction
anyway: dockets are invisible to it, and its dominant contribution is
re-references.

**When to revisit:** when TREATMENT QUALITY becomes the priority rather than
graph size. Those 1,898 short-form contexts are each another passage where the
court engages with a case — and courts short-cite when ARGUING with a case, so
that is exactly where overruled/distinguished signal concentrates. At that
point the right build is the hybrid the wrapper docstring always described:
eyecite tokenizes reporter cites (all forms, all variants), OUR code keeps
dockets, self-cite/caption guards, resolution, treatment, quotes, and storage.
Cost to revisit: a real `pip install` into the app venv on prod (the deploy
path is `git pull` + restart and does NOT run pip).

### PARALLEL CITES — the one data gap that was capping ALL THREE states

AZ was initially blocked: measured on 700 AZ opinions, P.2d/P.3d resolved at
88% but **`123 Ariz.` official — the most common format, present in 87% of
opinions — resolved at 0%**, because of 27,201 AZ cites we held, 25,335 were
Pacific and only **151** were official. An extractor built then would have
captured ~40% and looked successful.

**Root cause: `load_reporter_cites` stores ONE cite per opinion** (whichever CL
listed first) and skips non-empty rows. One scoping decision, made months ago
for good reasons, silently capped the citation graph in every state.

**Fixed 2026-08-04 by `ParallelCite` + `load_parallel_cites`** (migration 0029).
Harvested all cites for our 119,224 clusters from CL's bulk citations export:
290,407 rows, of which **96,381 of 103,465 clusters (93%) carry more than one
cite**. Loaded 180,652 (AZ 57,194 / MN 89,915 / NH 33,543); LEXIS/WL/A.L.R.
database identifiers are skipped — they never appear as citations in opinion
text. Measured before → after:

| state | format | was | now |
|---|---|---|---|
| AZ | `Ariz.` official | 0% | **93%** |
| AZ | `Ariz. App.` | 0% | **98%** |
| MN | `Minn.` official | 3% | **94%** |
| NH | `N.H.` official | 5% | **95%** |

Consequences, all shipped the same day: the **AZ extractor** was built
(`citations_az.py`, registered), **MN's `Minn.` official** was moved from
excluded to in-scope and MN re-swept, and **NH went 71 → 75,051 edges** — its
extractor had always worked; it was starved of resolvable keys and that had
been misread for months as the feature being "data-starved."

**Lesson worth keeping:** NH's 71-edge graph looked like a corpus limitation
and was even documented as one. It was a *loader scoping* bug two layers away.
Same shape as the phantom judges — a number that reads as missing data pointing
at a correctness problem somewhere else. The tell both times was a figure too
small to be plausible (71 edges across 20K opinions that cite each other
constantly).

**A separate table, NOT columns on Opinion** — that table is 2.75GB and an
indexed ADD COLUMN there is the 9-hour unkillable rebuild from the VECTOR INDEX
attempt. ParallelCite is small and indexed on `cite`.

**Ops lessons this session:**
- **A `nohup` supervisor is still a daemon.** The chunked command survived
  fine, but I mis-read `ps` mid-chunk, concluded the wrapper had died, and
  launched a manual chunk that ran CONCURRENTLY with it. No damage (0 duplicate
  pairs — they hit disjoint ranges), but drive chunk loops from OUTSIDE with
  short-lived invocations. **The DB is ground truth, not the log** — chunk 5's
  edges were committed while its completion line never reached the log.
- **`manage.py check` does not catch a missing import** used inside a view.
  `views.py` referenced `OpinionCitation` without importing it; checks passed
  and it would have 500'd every opinion page. Same shape as the documented
  UnboundLocalError trap.
- **Filtering `OpinionCitation` by `source` alone times out** (errno 1969) —
  no index on that column, by design. Request-time code must narrow by
  citing/cited opinion first; batch probes must lift `max_statement_time`.

## Prior session (2026-08-03) — MN gap diagnosed; it is CourtListener's, not ours

Went in to run the MN backfill. **The backfill did not run, because the premise
was wrong** — the gap is upstream (full evidence in the header block above).
What this session actually established, so nobody re-derives it:

- **Three independent measurements agree**: CL's bulk export, our prod DB, and
  CL's live API all show MN 2020–2022 = zero, both courts. Control query
  (minnctapp 2016 = 1,231) proves the method. **CL's dockets.csv is empty for
  those years too** (`A20-*` absent entirely, `A21-*` = 15, `A22-*` = 120) — so
  there is no CL-side path at all, not even docket metadata to build URLs from.
- **The opinions are fine and free.** mn.gov serves them unwalled: verified
  three real 2021 PDFs at HTTP 200 `application/pdf`, and the MN parser reads
  them correctly with no changes (A21-0414 → 2021-11-22, Affirmed, author,
  2-judge panel, 4 statutes, precedential=False). **The ingest half of this job
  is already solved** — it's `ingest_pdfs --state MN --court appeals`.
- **The whole remaining problem is ENUMERATION**, and it is bot-walled:
  - Year directory (`/archive/ctapun/2021/`) → Radware 302, not an index.
  - Old `a<NNNNNN>.pdf` scheme → 404 for opinions (though it IS still used for
    `COAspectorders`, seen live in search results).
  - Live scheme `OP<case>-<mmddyy>.pdf` needs the filing date, so URLs can't be
    derived from a case number. Filing dates are always **Mondays** (verified
    across samples) — a real constraint, but 1,800 cases × 52 Mondays × 3
    categories is far too many probes to be acceptable.
  - `mncourts.gov` media paths → 403.
- **Probed the search UI directly (headed Chrome, persistent profile).** Two
  facts, both load-bearing for the build:
  1. **The GET form works.** `mn.gov/law-library/search/?query=<vivisimo>&…&v:sources=mn-law-library-opinions`
     renders real archive result anchors, so the flaky JS form can be bypassed
     by navigating straight to a constructed URL.
  2. **`start-date` / `end-date` are IGNORED.** A November-2021 window returned
     newest-first results. So date bounding has to go inside the Vivisimo
     `query` (`date:>… date:<…`) — **that syntax is still unverified.**
- **The bot wall bites on the SECOND automated navigation.** The probe's first
  load was clean; the next one drew a CAPTCHA. Rapid programmatic loads are
  exactly what trips it (as `scrape_mn_coa.py`'s comments already warned).
  **Do not iterate on query syntax with a tight probe loop** — each failed
  guess costs a CAPTCHA and degrades the profile's standing. Space loads out,
  and expect to solve challenges by hand.

**THE BUILD — DONE. `scripts/mn_scraper/backfill_mn_archive.py`.** Recipe that
works, verified end to end on 3,102 opinions:

```
# 1. residential browser collects URLs (attended; a human clears CAPTCHAs)
python scripts/mn_scraper/backfill_mn_archive.py --strategy filedate \
    --since 2021-01-01 --until 2021-12-31 --no-download --manifest mn2021.tsv
# 2. NFSN downloads the PDFs (they are NOT walled from a datacenter IP)
python scripts/mn_scraper/fetch_manifest.py --manifest mn2021.tsv --out /tmp/mn
# 3. ingest each court separately (chunk ~300/batch: no --max-runtime, and
#    3,000 PDF parses blow past the cull)
python manage.py ingest_pdfs --dir /tmp/mn/appeals --state MN --court appeals
python manage.py ingest_pdfs --dir /tmp/mn/supreme --state MN --court supreme
# 4. derived passes: extract_statutes, extract_holdings_text, resolve_judges
#    (--max-runtime 35, loop on the printed --min-id). suggest_tags must WAIT
#    for the overnight embed -- new rows carry a placeholder vector.
```

**The five things that made it work — every one was found by measurement, not
reasoning, and each would have silently corrupted the result:**

1. **`start-date`/`end-date` on the search URL are IGNORED.** A November-2021
   window returns newest-first results. The `filedate` strategy instead keys on
   the `mmddyy` stamp *inside the filename* (`url:112221`), which the index
   tokenizes. **Never trust a window** — the script parses each result's date
   from its filename and REFUSES a window whose results fall outside it.
2. **The two courts file on DIFFERENT DAYS.** COA files Mon (88.9%) / Tue
   (11.0%); Supreme files **Wed** (88.8%). The first sweep was Monday-only and
   returned **885 ctapun / 0 supct** while reporting success. Default is now
   all five weekdays — and because `--batch-days` groups a week into ONE query,
   extra weekdays cost no navigations.
3. **CALIBRATE AGAINST A KNOWN-GOOD PERIOD.** Running the scraper over 2016 Q1
   (which CL has complete) and diffing against our own DB is what exposed #2.
   Do this before trusting any future sweep. It also surfaced **17 appeals
   opinions we don't have at all** in a quarter CL considers complete.
4. **Normalize case numbers before comparing anything.** Legacy rows carry
   `NO. ` prefixes and unpadded sequences (`A15-178`) vs filename-derived
   `A15-0178`. Raw comparison overstated the gap by ~half.
5. **Reconcile the manifest against the DB afterward.** Batch summaries only
   give counts; reconciliation NAMES the missing. 5 of 3,108 failed: 2 scanned
   image-only PDFs, 2 with PDF text-extraction damage (`F\niled`,
   `De cember`), 1 whose companion-case number confused the parser. Left
   unfixed on purpose — loosening the date regex to tolerate broken words is
   the fuzzy-fallback change that historically produces WRONG dates.

**TWO LATENT PARSER BUGS this exposed — both because no MN Supreme PDF had
ever been parsed** (Supreme opinions always arrived pre-parsed from CL bulk
CSV, so `parsing/mn.py` was written and tested against COA documents only):
`FILED_DATE_RE` required whitespace after "Filed" but Supreme writes
"Filed:&nbsp;&nbsp;<date>" → **234 of 240 errored**; and the caption extractor
took the first block after the docket number, which on a Supreme opinion is the
PANEL → every page would have been titled "Court of Appeals Chutich, J.
Concurring in part…". **Lesson: a code path that has never executed is not
working, it is untested** — the COA path had 60K opinions of validation behind
it and looked identical from outside.

## Prior session (2026-08-02) — pre-launch audit + truth-in-copy pass

Asked "is CLAUDE.md up to date, and is the site ready to show CourtListener?"
Audited prod end-to-end, then fixed what the audit found in the public copy.
**No data or parser changes this session** — copy, docs, and repo hygiene only.

- **Site health: GOOD.** Every public page 200s on all three subdomains (home,
  /opinions/, opinion detail, cited-by, judge, current-judges, tags, about,
  how-we-differ, privacy, support, robots, llms.txt, sitemaps, apex). No 500s.
  All three states current: MN 2026-07-27, NH 2026-07-31, AZ 2026-07-30.
- **Search latency is fine ALONE and terrible under CONCURRENCY.** Uncontended:
  MN 2–3s, NH 1–5s, AZ 1–2s. But with just **two** concurrent search sessions,
  one query hit **183s** (reproduced on both AZ and MN), and sibling
  connections died with errno **188** and **1969** — the documented poison
  cascade. One gunicorn worker × 8 threads. **This is the #1 operational risk
  for any launch moment** (HN/CL traffic = many simultaneous common-term
  searches). Not fixed this session; nothing safe to do quickly.
- **THREE public claims were false or overstated. All corrected:**
  1. **The eyecite claim was the worst one.** `how_we_differ.html` said
     "Citation extraction (non-NH states) **uses** eyecite," `about.html`
     credited it, and `apex.html` listed it in indexed `isBasedOn` JSON-LD.
     **Nothing imports `parsing/citations_eyecite.py`** — `requirements.txt`
     itself says "PREPARATION INFRA ONLY." MN/AZ edges come from CL's bulk
     citation-map. Claiming to use Free Law Project's own tool, to Free Law
     Project, was the single worst look on the site. Copy now describes the
     real provenance and explicitly says eyecite is vendored but not wired.
  2. **"Every opinion is currently being reviewed and tagged by a human
     editor"** — actual: 2,116 tags applied, 50,287 pending. Now states plainly
     that most records have not been read by an editor, and leans on the
     per-record status indicator as the honest answer.
  3. **"Uploaded same-day on Minnesota's Monday/Wednesday release schedules"** —
     the MN scraper is weekly, and the next sentence already said "weekly."
     Now: weekly, "current to within a week."
  Also fixed: AZ's "Supreme Court byline format pending a follow-up parser
  pass" (shipped long ago; AZ has 29K+ panel votes) and the "Three states as of
  June 2026" stamp.
- **MN gap now DISCLOSED, not hidden.** New "Known coverage gaps" section on
  `/about/` naming 2017–2023 incomplete + 2020–2022 empty, repeated in the
  status list and the FAQ JSON-LD. **Do not remove this until the backfill
  actually runs** — the About page previously said "full appellate corpus …
  1851 to current," which reads as completeness. Disclosing a gap is a
  strength in front of FLP; being caught with one is not.
- **Holdings are now DOCUMENTED on `/how-we-differ/`** (`#holdings`, "The one
  panel that looks generated, and isn't"). This closes the gap the panel's own
  template comment flagged: the opinion-page link was deliberately generic
  ("How we work") because that page had no holdings section. It now deep-links
  to `#holdings` and reads "How we find the holding." The section describes the
  EXTRACTIVE method only (verbatim quote, no-render-when-absent, the
  deliberately-unrun LLM pass) — **it is NOT the parked LLM rewrite**, which
  must still never ship.
- **LICENSE + README added.** The About page had claimed "open source …
  forkable" while the repo had neither file (no license = all rights reserved,
  which is not open source). License is **AGPL-3.0 — Onion's call, chosen to
  match CourtListener's own license**; canonical text pulled from SPDX
  (gnu.org was unreachable from both here and NFSN). README documents the
  corpus, the no-generation posture, query privacy, FLP attribution, the known
  gaps, and local setup.

**Audit findings NOT fixed (deliberate — they need real work, not copy):**
1. **MN 2017–2023 backfill.** The launch blocker. See the header block.
2. **Concurrency/search capacity.** See above.
3. **14,428 synthetic `CL-<id>` docket numbers** — a CL engineer recognizes
   their own cluster IDs on sight, and those pages aren't reachable by the
   identifier a lawyer would paste. Needs the in-place rewrite pass
   (`update_or_create` would duplicate).

## Prior session (2026-07-27) — judge-data integrity

Started as "complete the surname-only AZ judges via CL's people DB," turned
into finding that a chunk of the judge roster was never real. Full detail in
`docs/TODO.md` Tier 1; the essentials:

- **`resolve_judges` was minting CITED judges as panelists.** The CL crack
  exposed it: it matched "Scalia" → Antonin Scalia, and Brennan/Kennedy/White/
  Thomas are all SCOTUS. Two independent leak paths, both closed:
  1. The AZ top-of-opinion byline block matched *descriptions of other courts'*
     authorship ("Justice Scalia authored a dissent, in which Justice Thomas
     joined"). Now anchored to this court's own attribution ("… of the Court" /
     "the Court's opinion"). Recovers all real bylines (+312 McMurdie/Williams),
     drops citation blocks.
  2. The NH-style footer path ("SURNAME, JJ., concurred") is **structurally
     identical** to a cited SCOTUS lineup "(Scalia, Thomas, JJ., concurring)" —
     verb form, position, and last-match ALL fail to separate them (measured,
     not assumed). The only reliable signal is the name, so
     **`_CROSS_COURT_JUSTICES`**: 55 SCOTUS surnames *minus* any colliding with
     a real MN/NH/AZ judge (that subtraction protects NH's Souter, AZ's
     Miller/Stevens, MN's Murphy). Applied ONLY to the weak footer path, never
     the corroborated byline block. Residual = surnames shared with a real
     local judge; inherent ambiguity, documented, bounded.
- **29 phantom judges culled**, then a full AZ `--create-missing` sweep minted
  **zero** stoplisted-surname judges — the fix verified at scale, not in theory.
- **Two new merge commands, both dry-run-by-default (`--apply` to commit),**
  sharing `opinions/judge_merge.py`:
  `merge_hyphenated_judges` (PDF line-break artifacts: "Struck-meyer" →
  "Struckmeyer", only when de-hyphenating matches an existing same-state
  surname, so genuine "Smith-Florez" is untouched) and `merge_duplicate_judges`
  (exact-name dupes + bare-surname rows shadowing their full-name row, only
  when the surname has ONE unambiguous full name in that state). Conflicting
  first names are **reported and skipped, never fused**. Merges carry editorial
  metadata FORWARD and pick the metadata-rich row as survivor, so a seated bio
  row is never deleted in favor of a vote-only stub.
- **`surname()` took the last whitespace token — which is the SUFFIX** for
  "John T. Broderick Jr", splitting a judge's full-name row from their bare
  surname row so the merge never grouped them. Now skips Jr/Sr/II/III/IV/2/3/4,
  and `_norm` drops periods/commas so "Jr" == "Jr.". Recovered 432 AZ votes.
- **NH roster deep-clean: 63 → 36 judges** (OCR-corrupted name variants merged
  by byline confirmation; circuit-judge and clerk false-positives deleted;
  "Hantz marconi" casing fixed). NH's judge count dropping by 40% is the
  *correct* number — the old one counted artifacts.

**Lesson worth keeping:** the phantom judges had been visible in the data for
weeks as "weak coverage" (single-name judges, low vote counts) and were read as
a *coverage* problem. They were a *correctness* problem pointing the other way —
not missing data, invented data. When a data quality metric looks bad, check
whether the bad rows are real before building machinery to complete them.

**Follow-up 2026-08-07 — the AZ "double the judges" cleanup + the root fix.**
AZ carried 247 judge rows vs MN 122 / NH 37; the inflation was three extraction
defects, not real judges: junk tokens (And/Appel/Opinion/State), cross-court
citation leaks (Kozinski/9th, Dietzen/MN, Titone/NY — cited judges minted as AZ
panelists), and OCR name-splits (Údall/Udaljl→Udall). `cleanup_az_judges`
(one-shot, verified pk lists) actioned the high-confidence tier → 247→202.
**The durable lesson is the discriminator for cross-court leaks: a
`SURNAME, J., concurring` that sits INSIDE parentheses is a citation to another
court; a bare one is this court's panel signoff.** `_CROSS_COURT_JUSTICES`
only ever stoplisted SCOTUS surnames, so circuit/state judges leaked; the
name-agnostic `_inside_open_paren` guard in `resolve_judges` now catches all of
them at extraction time. It correctly KEEPS the real 19th-c. Arizona Territorial
justices (Tweed/Sloan/Pinney — bare signoffs). Two method notes that bit here:
the dry-run's evidence dump is what caught its own false positives (a naive
"SURNAME, J., concurred" rule nearly culled the Territorial justices; dist-2
"OCR" matching false-matched Fink→King, Arabian→Fabian where Arabian is a
*California* justice — a leak); and `filter(case_number=X)` alone table-SCANS
(case_number isn't the leading column of the `(court_id, case_number)` index) —
narrow by court_id or lift max_statement_time when probing by docket.

## Prior session (2026-07-24 → 25)

Long session — closed the last big per-state gaps, shipped extractive holdings,
built + scheduled the MN scraper, and solved the "MN gets zero AI traffic"
puzzle. Everything below is committed + deployed unless noted.

- **Extractive holdings — LIVE on all three states (39,402).** New
  `opinions/parsing/holdings.py` + `extract_holdings_text` quote the court's
  OWN holding sentence VERBATIM (not an LLM summary) — the ~$500 Haiku plan was
  dropped. Coverage MN 18,507 / NH 7,321 / AZ 13,574; `holding_model` records
  the extractor. Two bugs were caught by READING THE RENDERED PAGE: a decimal
  in a citation split the sentence ("rule 24.03" → "rule 24."), and restated
  holdings joined (fixed with word-set overlap dedup). Panel shows on all
  states now (gate lifted). **Do not reintroduce "summarized"/"AI" copy** —
  it's extraction.
- **AZ parser BUILT — `opinions/parsing/az.py`, registered.** There was NO AZ
  parser, so `backfill_dispositions --state AZ` was a silent no-op. **AZ
  dispositions 4.2% → 67.7%** (25,779). Both courts + both COA divisions.
  Disposition = the ALL-CAPS header line, anchored to a whole line; on a
  Supreme PR case take the FIRST (merits/superior-court result), not the later
  COA-below disposition. Tail fallback (0.5) catches the special-action
  (CA-SA) / PRPC classes that dispose in prose ("grants review but denies
  relief" → Denied), hardened to read the LAST operative verb + reconstruct
  "X in part" compounds (a subsidiary "we dismiss ... otherwise affirm" was
  storing "Dismissed"). Modern coverage 2020s 94% / 2010s 87%; the ~12K
  no-match remainder is genuinely historic AZ text.
- **MN COA scraper — built, SCHEDULED, and test-fired.**
  `scripts/mn_scraper/scrape_mn_coa.py` + `run_mn_weekly.ps1`, mirroring NH.
  Registered as Windows Task **"DocketDrift MN COA weekly scraper"** (Monday
  17:00, run-only-when-logged-on). Test-fired live: 3-page pagination works,
  no CAPTCHA (persistent profile banks clearance), 13 new opinions ingested,
  10 correctly deduped. MN COA current through 2026-07-21. See the dedicated
  scraper block further down for the CAPTCHA/pagination mechanics.
- **MN parser now handles BOTH order-opinion layouts** (`parsing/mn.py`). The
  scraper pulls a class the CL feed never did: **"SPECIAL TERM ORDER"** opinions
  (`a26xxxx`-style — motions, HRO/ERPO appeals, procedural dispositions). These
  are **caption-FIRST** (parties right under "IN COURT OF APPEALS", then the
  order header, THEN the case number) — the reverse of a regular opinion and of
  the older "ORDER OPINION" format the parser knew (`ab27442`). They were
  ingesting with an **empty case name** (blank CASE cell on the MN landing) and
  a **wrong precedential=True** flag. Fix: a case-name fallback that reads the
  caption from between the court header and the order header and normalizes
  "<P1>, Respondent, vs. <P2>, Appellant." → "P1 v. P2" (also the "In re ..."
  no-vs. shape); plus broadened the nonprecedential matcher to accept "SPECIAL
  TERM ORDER" + "this order is nonprecedential", dropping a trailing `\b` that
  failed on the fused footnote marker ("ORDER1"). Regular opinions unchanged;
  re-parsed the 7 affected rows. **Any new order opinion the weekly scraper
  pulls now names + classifies correctly** — don't reintroduce the case#-anchored
  caption assumption.
- **Discoverability — the "MN gets zero live-AI traffic" root cause + FIX.**
  Live AI grounds by web-search-then-fetch, so it only reaches INDEXED pages.
  Access-log cross-tab showed MN search-engine crawls = 1, NH = 1,421.
  **NONE of the docketdrift domains were in Google Search Console** — NH's
  crawls were purely ORGANIC. Two-part fix, both DONE: (a) AZ's sitemap emitted
  space-carrying dockets raw ("1 CA-CV 25-0606 PB" → invalid URL); now
  percent-encoded with canonical/og:url matching + host-specific robots
  Sitemap line (`657aa8a`); (b) owner added `docketdrift.com` as a **Domain
  property** (one NFSN DNS TXT covers apex + all subdomains) and submitted the
  mn/nh/az sitemaps. Now MONITORING (days: Googlebot on mn/az; weeks: re-run
  `ai_citation_profile`). Full status in `docs/TODO.md`.
- **Citation clustering — SHIPPED NH-first, but data-starved (a caveat that
  matters).** "How this document has been cited" (verbatim citing passages,
  Scholar-style collapse) is live on NH. BUT it has data on only ~41 NH
  opinions: the `OpinionCitation` graph resolves only the neutral-cite era
  (2024+) — older cross-cites ("141 N.H. 271" / A.2d formats) don't match a
  `reporter_cite`. **The real high-value citation work is improving RESOLUTION
  of those older formats**, which would light up both this panel AND the
  already-live cited-by graph corpus-wide. Embed cost was $0.000 (65 quotes).
- **Holdings review admin — salvaged + de-LLM'd, then REVERTED.** It's the
  missing editorial surface, but its `exclude(holding_summary="")` +
  `order_by(-holding_extracted_at)` scans the whole 119K corpus past the 25s
  cap (poison-cascade risk). **Needs an index migration on the 2.75GB
  opinions_opinion table** to ship — deferred. Also caught a latent bug:
  `.only("court__state__id")` — State's PK is `code`, not `id`.
- **Small wins:** Twitter/X cards showed the generic title on every page —
  `twitter:title`/`description` used a Jinja `{{ self.og_title }}` idiom that
  no-ops in Django; removed them so X falls back to the (working) `og:*`
  (`3aef…`). State-router middleware now memoizes the subdomain→State lookup
  per worker (was a DB hit every request). Beta/Flagship relabel: **MN is now
  the Flagship**, NH+AZ get a green "Live" pill (new `.status-pill--live`) —
  "beta" undersold three mature corpora.

- **Code review at session end found + fixed THREE data-integrity bugs**, all
  in the fuzzy fallback/cleanup heuristics (the "last 10%"): (1) `az.py`
  tail-disposition mislabeled full Affirmeds as "Affirmed in part" (bare
  `\baffirm` matched "affirmative", `\brevers` matched "reversible") + relief
  wasn't anchored to the deciding court → **recompute corrected 2,311 / cleared
  1,462 AZ dispositions; the false "mixed" inflation halved (4,117→2,074)**;
  (2) `holdings.py` restatement dedup dropped genuinely-distinct holdings that
  shared the appellate frame (now compares CONTENT words, frame excluded);
  (3) `holdings.py` page-number strip deleted the "N" in "denied 3 of the
  motions" (now keeps a digit before partitive "of"). All re-extracted. The
  core/structural code (scraper, middleware cache, sitemap encoding, header
  disposition path, sentence boundaries) reviewed CLEAN. **Lesson: the fuzzy
  fallback paths are where the bugs live — review those hardest.**

**Lessons this session (each bit me):**
1. **When the network to a state is flaky, verify web renders IN-PROCESS** with
   Django's test `Client`, not curl. The NH intra-rack route dropped response
   bodies all session (server logged 200, curl timed out at 40s) — I nearly
   rolled back a working feature over it. `render_check.py` pattern: `Client().
   get(path, HTTP_HOST="nh.docketdrift.com")`.
2. **Cherry-pick parked features; never blind file-copy.** `git cherry-pick -n`
   does a 3-way merge that preserves concurrent main edits (my Beta/Flagship
   change to `views.py` survived); `git checkout branch -- file` would have
   clobbered it.
3. **A parked feature that "looks finished" may be built at a scale/assumption
   that no longer holds.** Both parked features were: holdings admin was
   NH-scale (7K), citation clustering assumed a dense graph. Validate against
   the CURRENT corpus before shipping.
4. **NFSN `freshness-check` task had a broken command path** (`me/private/...`
   missing the `/ho`) — silently failed every Monday. Owner fixed it. When a
   scheduled task "runs" but nothing happens, check the exact command string.

## Latest session (2026-07-19)

**Headline: the NH disposition gap is closed — 0.2% → 78.5%** (46 → 16,255 of
20,720). It was two problems stacked, and the second one was a data-integrity
bug, not a coverage gap.

- **`backfill_dispositions --state NH` had simply never been run.** The NH
  parser was fine all along; a dry run scored 299/300. One command took NH from
  0.2% → 54.5%. (CL's `bulk_create` bypasses `Opinion.save()`, so the parser
  save-hook never fired — the same reason MN needed this backfill originally.)
- **The residual 9,419 were a different century, not a bug.** Pre-1980 NH
  opinions close with terse procedural dispositions — *Exceptions overruled.* /
  *Case discharged.* / *Judgment on the verdict.* / *Demurrer sustained.* — not
  the modern *Affirmed.* one-liner. The break is sharp: modern text matches at
  ~99%, pre-1980 at near zero. `parsing/nh.py` now has a historic tier
  (`HISTORIC_DISPOSITION_RE`), stems frequency-ranked from a scan of 6,000
  unmatched opinions; the sub-10-occurrence tail is left unmatched on purpose.
  **This matters commercially** — the instrumentation says 21% of live AI
  fetches are 1930s NH cases, so historic NH is disproportionately what AI
  grounds on.
- **TWO structural gotchas the extraction depends on** (both bit me):
  1. The literal LAST sentence of a historic opinion is the **concurrence
     footer** ("All concurred.", "BRANCH, J., did not sit: the others
     concurred."), NOT the disposition. `_disposition_sentence()` strips
     stacked footers first, then takes the final sentence. A naive
     last-sentence read returns "all concurred." 3,347 times.
  2. The historic match is **anchored to that whole sentence**, not a substring
     search. "new trial" / "motion denied" are everywhere in ordinary body
     prose, so an unanchored search mints dispositions the court never entered.
     Verified 24/24 on observed vocabulary, 0 false positives on prose.
- **The 0.4-confidence "modern token anywhere in the body" fallback was
  writing WRONG dispositions.** On a historic opinion it matched a passing
  mention of some case the court affirmed below: 1979 opinions whose actual
  disposition is *Exceptions overruled* were stored as **Vacated / Dismissed /
  Remanded / Affirmed**. Not weak — wrong, and a misstatement of the record.
  Tiering is now modern-tail (0.85) > historic (0.80) > body (0.40).
- **`backfill_dispositions` gained `--recompute` + `--min-confidence`** — it
  only ever filled EMPTY rows, so a bad value was permanently stuck. Repair run
  (`--recompute --min-confidence 0.8`): **5,388 filled, 363 corrected, 434
  cleared, 10,504 unchanged, 4,465 genuine no-match.** Clearing (rather than
  keeping) a value the parser can't justify is the deliberate choice — blank is
  honest, a stale wrong disposition is not. **Always `--dry-run` first**; the
  summary breaks out corrected/cleared/unchanged.

**Editorial rule set this session (Onion's call — follow it):** historic
dispositions are **transcribed, never mapped**. None of the historic stems
carry an affirmed/reversed/vacated token, so `compute_disposition_bucket()`
files them all under `other` (neutral tan) automatically — no special-casing
needed. Recording what the court wrote is transcription; deciding that
"exceptions overruled" *means* "affirmed" is an editorial read of the record
and is **not ours to make**. Don't "improve" this later by adding a mapping.

Verified end-to-end on the live site (not just in the DB): `/opinion/78-263/`
and `/opinion/No. 78-207/` both render 200 with the transcribed disposition and
`disposition-other`.

### Holdings are LIVE on NH — and they are EXTRACTED, not generated

The parked holdings feature shipped, but **not** the way it was built. It is
populated deterministically; the ~$90 Claude Haiku batch was **not run and is
not needed for the bulk of the corpus**.

- **Why.** A corpus scan showed courts announce holdings with a small stable
  phrase set — `we conclude` 80.9%, `we hold` 14.3%, etc. An LLM summary of an
  opinion that already says "We hold that X" is a lossy, unverifiable
  paraphrase of a sentence we can quote **exactly**. New
  `opinions/parsing/holdings.py` + `extract_holdings_text` do that.
- **The $90 figure was stale** — it assumed Haiku 3 pricing. The command is
  pinned to `claude-haiku-4-5` at $1/$5 per M, so the real cost is **~$88 for
  NH alone, ~$500 corpus-wide**. The LLM `extract_holdings` command is still
  in the tree, unrun, for the residual if ever wanted.
- **Result: ML stays in exactly TWO places.** Holdings do NOT become a third
  ML surface, so the `/how-we-differ/` disclosure remains true.
- **LIVE ON ALL THREE STATES — 39,402 holdings.** Final measured coverage
  (always read the modern column; overall is dragged down by pre-1980 text
  that predates this vocabulary entirely):

  | State | overall | modern (≥1980) | ¶ anchors |
  |---|---|---|---|
  | MN | 18,507 / 60,379 (30.7%) | **51.7%** | 49 |
  | NH | 7,321 / 20,720 (35.3%) | **72.9%** | 181 |
  | AZ | 13,574 / 38,074 (35.7%) | **41.0%** | 5,115 |

  AZ landed at 35.7%, far above the 21% I'd estimated from a sample — the
  sampling bias cut the other way there. AZ carries by far the most ¶ anchors
  because its opinions use court-assigned paragraph markers; MN almost never
  does, so MN holdings deep-link rarely. That is correct behavior, not a bug:
  we only ever emit the court's own [¶N].
- **Frequency alone is a trap.** `accordingly, we` (47.6%) is the DISPOSITION
  sentence; `we agree`/`we disagree` (34%/32%) characterize a party's
  argument. Excluded on purpose — including them triples coverage and wrecks
  precision. Matching is **anchored to the whole sentence**, never a substring.
- **The public copy was WRONG and is fixed.** `opinion_detail.html` (already
  committed, dormant) said "Summarized by Claude Haiku" in three places. It
  now reads "The holding / in the court's own words" in a `<blockquote>`.
  **Do not reword it to say "summarized"/"AI" unless the populator changes** —
  `holding_model` records which extractor produced each row.

**PARKED AND MUST NOT SHIP AS-IS:** the `about.html` / `how_we_differ.html`
rewrites describe the LLM version and assert a generated surface **in indexed
FAQ schema.org JSON-LD**. Shipping them now would publish false architecture
claims. `/how-we-differ/` has no holdings section yet, so the panel links to a
generic "How we work" — retarget once that page documents the extractive
method. Also still parked: the whole citation-clustering FEATURE code
(`cluster_citations`, `embed_citations`, `opinion_cited_by`, views/urls/
templates). Its **schema (0027) IS applied** — columns exist, feature dark.

### Two lessons from this run — both bit me

1. **`alter_algorithm='NOCOPY'` is NOT a fast-path guarantee.** It rules out
   the unkillable COPY rebuild (worth having), but permits INPLACE, and
   InnoDB's INPLACE ADD COLUMN still rebuilds the table. Migration 0026 took
   **39 minutes** for 7 columns on `opinions_opinion`. It completed, stayed
   killable, and the site served reads throughout. Use `'INSTANT'` if you want
   to fail in one second instead — but INSTANT can't cover an indexed column,
   so split that into ADD COLUMN + separate online CREATE INDEX.
2. **`.iterator()` order skews modern — always measure coverage BY ERA.** I
   quoted 86.6% NH holdings from a 2,500-row sample and the real number was
   35.3%. The identical mistake appeared in the disposition run earlier the
   same day (99% on the first chunk, 36% after). On this corpus a leading
   sample is not a random sample.

### Two holdings bugs found by READING THE RENDERED PAGE, not by test

Both fixtures were too clean to expose these. Verifying on the live site is
what caught them — keep doing that.

1. **Numbered citations were being truncated.** The sentence-boundary
   abbreviation guard covered known words and single letters but not decimals,
   so "rule 24.03" split at "24." and the panel quoted the court as saying
   "not precluded by rule 24." **That is a misquote of the record** — the
   exact failure the module exists to prevent. A period between two digits is
   now always a decimal point. Same fix covers "Minn. Stat. 609.185".
2. **Restated holdings were joined.** Courts restate to lead into the next
   section ("Because we conclude that X, we need not reach Y"). Exact-match
   dedup missed it; prefix comparison would too, since the restatement opens
   with a different connective. Now compares word sets at 0.75 overlap, with
   tokens punctuation-stripped ("24.03" vs "24.03," and differing final
   periods were counting real matches as distinct, dragging a true
   restatement to 0.73 — just under the line).

   **KNOWN LIMITATION:** long real-world restatements can still fall under
   0.75 and render as two similar sentences (see MN `A25-1808`). Left as-is
   deliberately — it is cosmetic redundancy of the court's own words, and
   tightening the threshold starts dropping genuine second holdings ("We hold
   X" + "We further hold Y"). Do not tune this without checking both sides.

### WE WERE INGESTING ~10% OF OPINIONS — ON EVERY STATE (found 2026-07-20)

Chased down from "why is MN a month behind". It was never an MN problem.

`iter_clusters_for_court` listed via **`/search/?type=o`**, which is
Elasticsearch-backed and returns a fraction of what exists. Same day, same
court, same window:

| court | `/search/` (what we used) | `/clusters/` (authoritative) |
|---|---|---|
| arizctapp since 2026-06-01 | 13 | **137** |
| minnctapp since 2026-06-01 | 4 | **37** |

**The missing records are the UNPUBLISHED / nonprecedential ones.** MN merely
surfaced it first because its volume is high enough that losing 90% reads as a
stale date; AZ looked healthy because the few that arrived were recent.

The old code chose `/search/` on the stated grounds that "`/clusters/` doesn't
whitelist `court` OR `docket__court` (both return 400 unknown_params)". **That
is no longer true on v4** — `docket__court` filters fine. The workaround
outlived the problem. Now fixed to list from `/clusters/`.

**THIS LIKELY INVALIDATES THE "MN COA COURTLISTENER GAP".** `docs/
MN_COA_BACKFILL.md` and the roadmap say CL doesn't carry recent MN COA
nonprecedential/order opinions, and that a residential mn.gov scraper is
needed. But CL *does* carry them — we just weren't asking for them. **Re-test
that premise before building the scraper**; it may be largely unnecessary.

Two more things this exposed:

- **14,440 opinions (~12% of corpus) have a synthetic `CL-<id>` case_number.**
  `case_number` is the URL key AND what paste-a-docket search matches, so
  those are unreachable by the only identifier a lawyer has, rendering as
  `/opinion/CL-10878289/`. Cause: `/clusters/` doesn't denormalize
  `docket_number` (only `docket_id`), so ingest fell back to the cluster id.
  Fixed going forward via `fetch_docket()` (cached per run). **NOT
  retro-fixed** — `update_or_create` keys on `(court, case_number)`, so
  re-ingesting under corrected numbers creates DUPLICATES beside the CL- rows
  instead of repairing them. Needs a deliberate repair pass that rewrites
  case_number in place.
- **CL serves future-dated records.** An `arizctapp` cluster was stamped
  **2026-10-20** on 2026-07-20. One of those poisons every "newest opinion"
  display and **silently defeats `check_freshness`** (which measures staleness
  from the newest row — a future date makes a dead pipeline look current).
  Now dropped at the client boundary.

**Gotcha for the catch-up:** `--limit` now bounds PAGINATION, not just
processing. Each cluster costs a `fetch_opinion` per sub-opinion plus a
possible `fetch_docket`, and CL answers bursts with multi-hour backoffs (a
**21-hour** one is in the June logs). Unbounded catch-up = cooldown.
Also: deleting Opinions cascades widely enough to drop the connection
(errno 2013) — delete in small batches with retry-reconnect.

Verified: MN COA newest went 2026-06-22 → **2026-07-06**, `/opinion/A25-2082/`
and `/opinion/A25-1259/` both 200, zero CL- rows in the window, and the
already-correct `A25-1808` was UPDATED not duplicated.

### MN COA scraper BUILT + VALIDATED (residential Chrome, mirrors NH)

`scripts/mn_scraper/scrape_mn_coa.py` + `run_mn_weekly.ps1`. An owned,
debuggable pipeline independent of CL — the whole reason it was built even
after the `/clusters/` fix (Onion: NH is the one source that "just works";
give MN the same). 19 real COA opinions (2026-07-20 + 2026-07-13) scraped →
ingested → live end-to-end on first use; MN COA newest 2026-07-06 → 2026-07-20.

- **How it works:** headed real Chrome pages `opinions-archive.jsp` (a
  newest-first list of ALL recent opinions, no search needed), keeps COA PDFs
  with row-date ≥ `--since`, downloads via in-page fetch → `scp` →
  `ingest_pdfs --state MN --court appeals`. The MN parser re-derives all
  metadata from each PDF.
- **The recon doc's URL scheme was STALE** — corrected in
  `docs/MN_COA_BACKFILL.md` "BUILD NOTES". Live paths are
  `archive/<cat>/<year>/OP<case>-<mmddyy>.pdf` (cats: `ctappub` `ctapun`
  `COAspectorders`; `supct` excluded), not `archive/<cat>/a<n>.pdf`.
- **Reliability lessons baked in:** never `networkidle` (a chat widget keeps
  the network live forever) — wait for result anchors with reload-on-empty.
  **Radware CAPTCHA** hits rapid reloads / deep pagination but NOT a fresh
  page-1 load; a persistent browser profile banks any clearance, and a CAPTCHA
  is **never auto-solved** — the scraper waits for the logged-on human (same
  "run only when logged on" model as NH). Page 1 is captcha-free → weekly
  forward-fill is reliable there; deep backfill is a separate attended sweep.

**Next-session pickup, in order:**
0. **Register `run_mn_weekly.ps1`** as a logged-on Windows Task Scheduler entry
   (mirror the NH task). Then the MN COA scraper is fully autonomous for
   forward-fill.
1. **MN COA deep backfill** (attended) for the thin years — walk the pager in
   bounded windows, solving the occasional CAPTCHA. Weekly forward-fill is done.
2. **Finish the CL catch-up ingest** (separate pipeline). Only the newest 12 MN
   COA clusters were pulled as a smoke test; work back through MN/AZ/NH in
   bounded `/clusters/` runs (`--since` + `--limit`), watching for 429s.
3. ~~**AZ disposition — 4.2%, NO AZ PARSER.**~~ ✅ DONE 2026-07-21.
   `parsing/az.py` written + registered (both AZ courts, both COA divisions).
   **AZ dispositions 4.2% → 67.7%** (25,779 / 38,074). Header path (ALL-CAPS
   disposition line, take the FIRST = merits result not the COA-below line) at
   0.9; tail fallback at 0.5 for the special-action (CA-SA) / PRPC classes that
   dispose in prose ("grants review but denies relief" → Denied) + older
   opinions. Modern coverage 2020s 94% / 2010s 87%; the ~12K no-match remainder
   is genuinely historic AZ text (the NH diminishing-returns pattern). Bucket
   mix is legally sane (affirmed ~50%). The parser also extracts the author
   byline (Supreme "JUSTICE X authored"; COA "Judge/Presiding/Vice Chief Judge
   X delivered|authored").
   **AZ judge/panel extraction — DONE 2026-07-24. Panel votes 142 → 29,089**
   (opinions with a panel 42 → 11,265). The "142" was NEVER a coverage gap —
   it was a **catastrophic-backtracking (ReDoS) bug** in `resolve_judges`'
   `_PANEL_GROUP_RE`: a whitespace-only-nullable separator in the surname list
   made a whole era of 2017-2019 AZ opinions take ~3.4s EACH in one regex, so
   every run burned CPU and got NFSN-CPU-culled before progressing. Fixed by
   requiring a comma/"and" between panel surnames (3.4s → ~0ms; verified 4,001
   opinions in the bad range now 0 slow). Along the way `resolve_judges` gained
   the machinery it needed to run at scale on NFSN at all: lifts
   `max_statement_time`, pre-resolves court IDs, pk-windowed fetch,
   `--max-runtime`/`--min-id`/`--id-batch` (cull-safe chunked resume). Full AZ
   sweep then ran in ~10 min / 12 chunks.
   **Gotcha for the driver:** NFSN's CPU cull sits at ~40s, so a chunk must
   self-exit under that; `--max-runtime 35 --id-batch 6000` is a safe combo.
   **FOLLOWUP (editorial) — PREP PASS DONE 2026-07-25.** `--create-missing`
   minted 280 UNKNOWN AZ judges from bylines (162 surname-only like "Becke",
   125 full-name, 38 zero-vote roster orphans, + PDF hyphenation dupes like
   "Struck-meyer"). Automated prep (data-only): **30 names completed** to full
   form (each verified against a byline in an opinion that judge AUTHORED, or a
   single unambiguous roster surname match — technique reusable), 6 orphan rows
   deleted, 8 hyphenation artifacts fixed (287→280 rows). Status left UNKNOWN
   so the human confirm still counts. **Left for Onion in admin:** confirm the
   30, complete ~3-4 mid-vote surname-only, cull ~32 zero-vote orphans. The
   131 remaining surname-only whose full name isn't in any byline/roster we
   hold are **QUEUED for a CL people-DB crack when CL's rate limit recovers**
   (don't run during a backoff; dry-run + eyeball, never auto-apply). Full plan
   in `docs/TODO.md` Tier 1.
2. **NH's remaining 4,465 no-match** are genuine one-off 19th-c. prose
   ("There must be a decree in favor of the plaintiffs..."). Diminishing
   returns; only worth another pass if a frequency scan shows a new cluster.
3. Everything below from the prior session still stands — MN COA scraper, the
   two unregistered NFSN scheduled tasks, tag-queue triage.

## Prior session (2026-07-12 → 07-18)

**Headline: MN + AZ are now citeable and AI-discoverable.** Reporter cites and
a 605K-edge citation graph landed for the two big corpora, both built OFFLINE
from CourtListener bulk files (zero API calls, no rate-limit trap). See
"Reporter cites + citation graph" below — that's the biggest change here.

Shipped this session (committed + deployed):

- **Reporter cites for MN/AZ — 103,349 filled** (`99fc63d`, `d9dbd10`).
  MN/AZ cites (N.W.2d, P.3d) aren't in our opinion text, so they came from CL's
  `citations-2026-03-31.csv.bz2` bulk export (127MB), matched by
  `courtlistener_id` (= CL **cluster_id**). Coverage now **MN 93% / NH 90% /
  AZ 75%** (the gap is unpublished opinions, which genuinely have no cite).
  `load_reporter_cites` is idempotent (fills empty only, so NH's parser-derived
  neutral cites survive; it also backfilled old pre-neutral NH A.2d/A.3d cites).
  **Paste-a-cite search worked instantly for MN/AZ** — the routing was already
  state-agnostic — and the cite now renders in the opinion header + meta.
- **Citation graph for MN/AZ — 605,353 edges** (`be4917d`). From CL's
  `citation-map-2026-03-31.csv.bz2` (522MB, = `search_opinionscited`). That file
  is keyed on CL **opinion** ids, so the mapping hop is
  opinion_id → cluster_id (from the subset `opinions.csv`, col 0 → col 21) →
  our Opinion. Kept only INTERNAL edges (both endpoints in-corpus), collapsed to
  case level, deduped: 77M edges scanned → 721K internal → 605K after scoping to
  MN/AZ citing opinions (NH skipped — it has its richer text-extracted graph).
  "Cited by" + "Authorities cited" panels now render on MN/AZ. **The graph is
  demonstrably correct**: it independently surfaced *Thiele v. Stich* as MN's
  most-cited case (900×) and *State v. Leon* for AZ (1,228×).
  NOTE: bulk edges carry no `context_quote`, so the "How this document has been
  cited" quoted-passage panel stays NH-only (that needs text extraction).
- **A docket number is NOT unique — and ~1,292 MN opinions had no URL**
  (`2f26452`, `f6d4381`). `opinion_detail`/`opinion_pdf` used
  `qs.get(case_number=...)`, which raised MultipleObjectsReturned and **500'd
  ~1,300 MN pages**. Root cause is NOT duplicate ingestion: a case keeps its
  docket number through review, so 1,292 MN case_numbers carry BOTH the Court of
  Appeals opinion AND the later Supreme Court opinion (only 32 are true
  same-court dupes). They're distinct opinions — deleting either loses real law.
  Fix: serve the **highest court** (Supreme supersedes), render an "Also decided
  in this case" link, and support `?court=appeals|supreme` — which is what makes
  the sibling reachable at all. Thiele's landmark Supreme decision
  (425 N.W.2d 580) had no reachable URL before this.
- **Instrumentation — we can now see how the site is used.** Three read-only
  tools, all privacy-clean (the access log is query-stripped, so they report
  WHICH opinions get fetched, never anyone's questions):
  `scripts/ai_citations.sh` (weekly digest: **live citations** — chatgpt-user /
  claude-user / perplexity-user, a human asked an AI and it fetched a page right
  then — vs **training crawlers**); `ai_citation_profile` (joins those fetches to
  DB metadata: what KIND of law AI grounds on); `corpus_insights` (disposition
  mix, caseload trend, most-cited, hot statutes). First findings in
  "What the data said" below.
- **gunicorn threads 4 → 8** (`a736fa7`, `run.sh`). Doubles concurrency without
  the memory risk that forced `workers=1` (threads share one process heap).
  Verified fast under heavy crawler load. Going to `workers=2` still needs the
  NFSN process-RAM ceiling confirmed in the member panel first.
- **`claude-user` + `perplexity-user` added to `INDEXER_CRAWLER_TOKENS`**
  (`fb31842`) so live AI retrieval agents skip the expensive cosine scan they
  never use — exactly the traffic we want to welcome cheaply.

- **NH + AZ now have editorial tags** — ran the tag-suggestion pipeline for the
  two states that showed "0 legal topics to browse". `embed_tags` was already
  done (32/32 tags embedded) so this was **$0 Voyage — `suggest_tags` is pure
  MariaDB cosine**, no API call. Results: NH **17 tags auto-applied / 7,552
  queued** for review; AZ **16 / 22,574 queued**. (MN unchanged: 19 applied /
  ~21K queued.) The pending review queue is now ~50K across all three — expected;
  the tag-review overhaul below is what makes it approachable.
- **`suggest_tags` gained `--state <CODE>`** (`575db19`) — mirrors
  `embed_opinions`/`extract_statutes`; scopes a run to one state's courts so you
  don't re-score every un-scored opinion corpus-wide (MN was only partially
  scored, so a global run would've dragged in ~50K MN rows). Also **lifts
  `max_statement_time` for the batch cosine scans** (`8edaec1`): the per-tag
  `VEC_DISTANCE_COSINE` passes are batch work but inherited settings' 25s web
  cap and died with errno 1969 under daytime contention — now `SET SESSION = 0`
  + a per-scan `SET STATEMENT` guard.
- **State-landing "0 legal topics" fixed** (`575db19`, `state_landing.html`) —
  the tags stat tile + Tags browse card render only when `total_tags_used > 0`
  (was a naked "0 … to browse" stat + a card that dead-ended to an empty page on
  NH/AZ); the card now cites the in-state applied count, not the whole vocabulary.
- **Tag-review admin overhauled to feel finishable**
  (`d3d22f5` + `4a77f2b`/`034c266`/`6bd8520`, `admin_views.py` +
  `admin/tag_review.html`). The 50K pile is now a **pile picker**: pick one tag
  "pile" (shown with its pending count), work it under a per-slice progress bar
  (+ an overall progress bar), **one-click bulk accept/reject** the whole
  filtered slice (confidence-sorted so the top is near-certain; bulk-accept
  gated to a tag/min-confidence filter so it can't blanket the low-confidence
  tail), a **state filter** (MN/NH/AZ), and **keyboard flow** (A accept / R
  reject / S skip / arrows, auto-advancing). Three perf lessons baked in: the
  state filter uses pre-resolved `opinion_id__in`, NOT `opinion__court_id__in`
  (the join scans the 2.75GB opinions table → 25s timeout on AZ); the no-tag
  landing is the picker only (no unfiltered `ORDER BY -confidence` over ~50K
  pending, which also removed a poison-cascade risk); the state→opinion-id
  resolution is lazy (runs only when a list is actually built).
- **Polish audit** written to `docs/POLISH_OPPORTUNITIES.md` (untracked) —
  return-heavy small fixes across the live site. Top finds: the "0 legal topics"
  bug (now fixed); the **Beta/Flagship labels undersell the 60K MN corpus**
  (stamped "Beta" while smaller NH is "Flagship"); **per-page Twitter-card meta
  is broken** (`{{ self.og_title }}` is a Jinja idiom that no-ops in Django
  templates → every X card shows the generic title); and **surface the POST-only
  query-privacy story on the Privacy page** (the strongest differentiator is
  invisible). Pick from it anytime.

**What the data said (run these again; they're free and rerunnable):**

- **AI already grounds on DocketDrift — 94% of it on NH.** In one week, live
  agents fetched 84 opinions (75 chatgpt-user, 9 claude-user): **94% NH, 6% AZ,
  0% MN**. Why? NH was the only state with reporter cites, and AI looks cases up
  BY cite. That was the evidence for doing the cite backfill above — **re-run
  `ai_citation_profile` in a few weeks; MN/AZ should now start appearing.** That
  is the cleanest available measure of whether this session worked.
- AI reaches for both new and foundational law: 32% of fetches were 2020s cases,
  but 21% were from the **1930s** (top single fetch: a 1932 NH case, 12×).
- **MN — the flagship — has a coverage hole.** Caseload by year: ~1,400/yr
  through 2016, then 438 (2017), 208 (2018), 176 (2019), **zero for 2020-2022**,
  115 (2023). That is not a filing-rate change; it's the documented MN COA
  CourtListener gap, now quantified. AZ (a "beta" state) has *more current*
  coverage than MN. This makes the MN COA scraper a priority, not a someday.
- **Disposition bucketing is a per-state gap:** MN **98%** populated (parser
  works), AZ **~4%**, NH **~0.2%**. So NH/AZ opinion outcomes render blank.
- **Hot statutes leaderboard is publishable content today** — e.g. Minn. Stat.
  § 645.16 (statutory-construction canons) ranks top-tier because it's cited
  whenever a court construes a statute; family/juvenile/civil-commitment
  statutes dominate the appellate docket across all three states.
- Small data glitches spotted: NH judge "Hantz **marconi**" (bad casing); the NH
  citation graph skews recent (only neutral-cited opinions resolved).

**Isolation discipline used this session — KEEP DOING THIS.** The working tree
carries substantial **uncommitted parked holdings work** (migrations 0026/0027,
`extract_holdings`, `admin/holding_review*`, plus the holdings-aware
`about.html`/`how_we_differ.html` rewrite) that must NOT ship until its backend
is on prod. `admin_views.py` + `docketdrift_site/urls.py` are **entangled**
(holdings + tag-review edits in the same files), so every tag-review commit was
isolated via a git worktree or `git stash push -- <file>`, with the holdings
changes restored after. If you commit in those two files, do the same — do NOT
`git add` the whole file, or you'll ship the parked holdings feature (whose
model fields aren't on prod → 500s).

**Next-session pickup, in order:**
0. **Register two NFSN scheduled tasks** (member panel, not scriptable — the
   only things blocking otherwise-finished work):
   `ai-citations` → `/bin/sh /home/private/docketdrift/scripts/ai_citations.sh 7`
   weekly (emails the AI-usage digest); and the still-pending `freshness-check`
   → `/home/private/docketdrift/scripts/freshness_check.sh` weekly.
1. **Fill the per-state gaps — "three states fully functional" is the goal.**
   Each state has ONE different weak spot now:
   **MN** = the 2017-2023 coverage hole (build the COA scraper; recon done in
   `docs/MN_COA_BACKFILL.md`). **NH** = disposition bucketing (~0.2%; MN's
   parser proves the field works). **AZ** = weak judge/panel extraction (only
   142 panel votes, single-name judges like "Becke") + missing COA judge bios.
2. **Triage the tag queue** via `/admin/opinions/tag-review/` (~50K pending) —
   work it a pile at a time with the new picker/bulk/keyboard flow.
2. **Harden the tag-review heavy slice (deferred this session).** A state +
   specific tag on a *big* state (AZ) can still be slow under DB contention, and
   the admin runs on the single gunicorn worker → a stalled query could ripple
   to the public site (the poison-cascade gotcha). The default paths are cheap
   now, so exposure is small; the clean fix is to self-bind those slice-count /
   list queries with `SET STATEMENT max_statement_time` + catch-and-close,
   mirroring `semantic.py`.
3. **Register the `freshness-check` NFSN scheduled task** (still pending from the
   prior session — member panel, not scriptable; see the prior-session block).
4. **Build the MN COA scraper** (still pending — recon done; see the
   prior-session block + `docs/MN_COA_BACKFILL.md`).

## Prior session (2026-06-28)

Shipped this session (committed + deployed unless noted):

- **Search multi-word bug FIXED** (`4bed859`, `views.py`). `_fulltext_candidate_ids`
  had wrapped the whole query in quotes → BOOLEAN-MODE *phrase* match → every
  multi-word search returned 0 sitewide (single-word was unaffected, which hid
  it). Now `_boolean_and_expr()` requires each term (`+term`) after stripping
  operator chars + dropping stopwords/sub-3-char tokens (a required
  `+stopword`/`+tooshort` zeroes the whole match — verified `+hro +of` → 0).
  Verified live: MN `default hro` 0 → matches.
- **MN parser handles "ORDER OPINION" format** (`ab27442`, `parsing/mn.py`).
  Order opinions date themselves `Dated: <date>` (not `Filed`), split the caption
  across blank lines, and carry a "this order opinion is nonprecedential" footer.
  Additive + regression-safe (only fires when the `Filed` path misses).
- **Rickmyer A25-0969 ingested** via `ingest_pdfs --state MN --court appeals`
  (MN COA court slug = `appeals`; MN Supreme = `supreme`). It surfaced a real
  gap: **CourtListener does NOT carry recent MN COA nonprecedential/order
  opinions** — not a search/ingest bug, a source-coverage gap. Full writeup +
  fix plan: `docs/MN_COA_BACKFILL.md`.
- **Freshness monitor** (`92516f6`): `check_freshness` command +
  `scripts/freshness_check.sh`. Non-zero exit (→ NFSN emails) when any live
  state's newest opinion is older than its threshold (MN/AZ 45d, NH 60d). The
  longevity safety net for the per-state scraper model.
- **NH scraper now SCHEDULED** (`107e965`): `scripts/nh_scraper/run_nh_weekly.ps1`
  chains scrape → scp → `ingest_pdfs --state NH --court supreme`. Registered as
  the Windows Task Scheduler task "DocketDrift NH weekly scraper" (Sun 17:00,
  **run only when logged on** — headed Chrome needs an interactive desktop).
  **Playwright is now installed in the repo `.venv` — LOCAL ONLY, deliberately
  kept OUT of `requirements.txt`; it must NEVER reach NFSN's FreeBSD.**

**Next-session pickup, in order:**
1. **Register the `freshness-check` NFSN scheduled task** (member panel, not
   scriptable): Tag `freshness-check`, Command
   `/home/private/docketdrift/scripts/freshness_check.sh`, weekly (Mon 12:00 UTC
   suggested). Turns the monitor on. This is the only thing blocking item 1.
2. **Build the MN COA scraper** — recon + feasibility DONE, scraper NOT written.
   Confirmed: headed real Chrome bypasses mn.gov's Radware bot wall (curl/NFSN
   get a captcha); PDFs download from NFSN at deterministic
   `//mn.gov/law-library-stat/archive/{COAspectorders|ctapun|ctappub}/a<NNNNNN>.pdf`
   (`a260529`=A26-0529; dirs = order/unpublished/published); case#+type from the
   URL, everything else from the PDF via the (now order-aware) parser. Build =
   residential Playwright like NH, driving `opinions-archive.jsp` (`query`
   field, `date:>YYYY-MM-DD`) → collect COA PDF URLs in range →
   `ingest_pdfs --state MN --court appeals`. GOTCHA: the search form/results are
   JS-injected + load-timing-VARIABLE → use robust `wait_for_selector`, not
   fixed sleeps (this is what makes it an iterative build). Full map:
   `docs/MN_COA_BACKFILL.md` "Recon findings". Then wrap (`run_mn_weekly.ps1`,
   mirror NH) + schedule + a one-time backfill sweep.

**Strategic threads explored this session (no code — captured in Claude memory):**
the **depth-over-breadth moat** (per-state residential scrapers beat FLP on
chosen states because the unscalable work is the moat); **monetization
direction** (subscription/MRR, ~250 subs @ $40 = comfortable solo salary,
completeness + privacy wedges); and a **privacy-preserving alerts** design
(structured-facet alerts only — judge/statute/court, anonymized, RSS-first,
identity decoupled; semantic/keyword alerts refused-by-design, not stored).

## Where things stand right now

(Numbers pulled live from prod 2026-08-07/08. **Re-measure before
quoting these anywhere public** — stale numbers on a public page are the
exact class of problem the 2026-08-02 audit was cleaning up.)

Three states live, all on subdomains of `docketdrift.com`. MN is the
**Flagship**; NH + AZ carry a green **Live** pill.

| State | Subdomain | Opinions | Newest | Notes |
|---|---|---|---|---|
| MN (flagship) | `mn.docketdrift.com` | ~69,800 | 2026-08-05 | **CONTINUOUS 2015–2026** (~970-1,435/yr); ~9,800 rebuilt from the State Law Library archive + 519 early-2026 (2026-08-07) |
| AZ (live) | `az.docketdrift.com` | 37,791 | current | COA now split **Div One 18,593 / Div Two 5,717**; Supreme 13,481. CL feed healthy; our ONLY state with no independent pipeline |
| NH (live) | `nh.docketdrift.com` | 20,723 | current | proving ground; steady state |

**AZ judge roster rebuilt 2026-08-07 (247 → 194).** Court split into Supreme +
COA Division One + Division Two; the current bench is fully seated — **35
judges** (Supreme 7, Div One 19, Div Two 9), each with full name / role /
division / official-bio link, from the courts' own rosters. `Court.division`
now models multi-panel systems. See the 2026-08-07→08 session block.

Corpus-wide: **~127,563 opinions** (−605 from the 2026-08-07 duplicate merge),
all embedded (slim table in exact sync).
Citation graph **1,462,119 edges** — 605,353 CL bulk + **856,766
text-extracted** (MN 468,595 / AZ 313,120 / NH 75,051) with context quotes;
**5,188 non-default treatments** (was 0 in every state before 2026-08-04).
Parallel cites 180,652. Holdings 41K+. Tags 2,116 applied / ~50K pending.

Caveats that stay load-bearing:
- **Rebuilt MN years are substantially covered, not provably complete**
  (~83% COA vs control; ~53% Supreme — the archive carries no Supreme
  ORDERS), and they have no reporter cites until CL backfills. Disclosed on
  /about/; keep it that way.
- **AZ judges 247 → 194 and NH 69 → 36 are CORRECTIONS, not losses.** The old
  counts included citation false-positives (a whole class of parenthetical-cite
  leaks fixed at the source 2026-08-07) and OCR/hyphenation duplicate rows.
  Same for AZ panel votes 142 → 29K+, which was a ReDoS bug, not a gap.
- **IA publication is a ZIP, not 10K loose files** — individual-file upload is
  ~9.6h and fragile on IA; the item holds one 1.63GB archive + manifest + README.

(Opinion counts as of 2026-06-27: 119,159 total, all embedded. The 2026-06-27
VECTOR-INDEX retry deleted 12 zero-`raw_text` metadata stubs — MN ids 2618,
42273, 40190, 12262; AZ COA-Div-1 ids 61027, 84965, 90696, 90826, 91002, 91103,
91159, 91179 — and embedded the remaining fresh ingests, leaving 0 NULL
embeddings. See the "MariaDB VECTOR INDEX is infeasible" gotcha.)

The apex `docketdrift.com` shows three live state tiles. About page is
trimmed; the full anti-hallucination disclosure + ML-architecture
breakdown live on `/how-we-differ/`. Judge pages carry a
votes-per-year SVG chart with `?vs=<other-slug>` overlay and a
"compare" link on every co-panelist; `/compare/judges/?a=&b=` is a
side-by-side dossier with a concordance + split-decision section.

**Citation engine (NEW, 2026-06-16 — our KeyCite/Shepard's layer, NH-first).**
`Opinion.reporter_cite` is each opinion's canonical cite (NH neutral cites like
`2026 N.H. 7`; populated by the NH parser, backfilled by
`backfill_reporter_cite`). Paste a reporter cite into search → routes straight
to the opinion (like statute/docket cites). `extract_citations` parses opinion
bodies for references to other opinions, resolves them against `reporter_cite`
into the `OpinionCitation` graph (citing→cited) with a regex-classified
`treatment` (followed/distinguished/overruled/criticized/explained; default
cited). `opinion_detail` renders a "Citing references / Authorities cited"
panel (`_treatment_panel.html`) with treatment badges. **NH-only so far** —
MN/AZ reporter cites are reporter-assigned post-publication and aren't in our
opinion text, so they await a CourtListener cite backfill. Files:
`parsing/citations*.py`, `parsing/treatment.py`, `extract_citations`,
`backfill_reporter_cite`, migrations 0024 (reporter_cite) + 0025
(OpinionCitation).

**NH is the proving ground** (Onion's rule, 2026-06-16): build + verify every
new feature on NH first — smallest, cleanest, self-resolving corpus (neutral
cites make its data self-referential in a way MN/AZ aren't) — then roll out to
MN/AZ. NH-first is the plan, not a compromise. The citation engine is the first
feature built this way.

**Cite-anchored deep links (2026-06-24, NH-first).** `format_opinion_text`
(`templatetags/opinion_text.py`) already emits `<p id="para-N">` + a `¶N`
self-anchor for any chunk that opens with a court-assigned paragraph marker
(`[¶N]` / `¶N` — NH/AZ Supreme convention; MN opinions rarely carry them).
That markup renders for ALL states, so `#para-N` native-scroll works
everywhere. The 2026-06-24 polish adds the NH-only UX layer in
`opinion_detail.html` (gated `state.code == 'NH'`) + `docketdrift.css`: a
`.para-flash` keyframe that pulses the target paragraph cyan and fades over
~2s (class-driven, not a `:target` animation, so re-clicking the same para
re-fires it; honors `prefers-reduced-motion`), flash-on-arrival +
flash-on-hashchange with smooth scroll, and each `¶N` pilcrow turned into a
click-to-copy share link (Clipboard API + `execCommand` fallback, "Copied"
bubble). The persistent `:target` left-border marker is unchanged on all
states. This is the URL-anchor substrate the citation engine will use once
pinpoint cites are extracted (cite → `/opinion/<docket>/#para-N`). MN/AZ
pilcrows stay plain navigate-on-click anchors (copy/flash JS is NH-gated).

**Other recent work (2026-06-15→23):** all judge portraits are now SELF-HOSTED
static assets, no hotlinks (`localize_judge_photos` + `scripts/fetch_judge_photos.py`);
NH Supreme justice cards populated + NH opinions current to 2026-06-11 (both via
the residential-Playwright `scripts/nh_scraper/`); `/current-judges/` browses
prior judges by decade (`?era=<decade>`/`all`, active spans derived from panel
votes); opinion PDFs serve via the `opinion_pdf` FileResponse view (NFSN doesn't
web-serve `/media/`); analytics added site-wide (GA4 at the time — **since
REMOVED in favor of goatcounter-only**; see "Data is sacred"). The 2026-06-16
landing/apex 500 outage was the `_state_landing_stats` date_range Min/Max
scanning the corpus under `court_id__in` — fixed to an indexed
`ORDER BY release_date LIMIT 1` (see the gotcha section).

**Embed (2026-06-15 redesign):** the self-respawning daemon wrapper is
GONE. Embedding is now driven by an NFSN **scheduled task** running
`scripts/embed_tick.sh` every ~10 min. Each tick runs ONE bounded pass
(`embed_opinions --max-runtime 480`), self-exits under NFSN's wallclock
cull, and the next tick resumes via the indexed `embedding_pending`
flag. Target state lives in `.embed_state` (one USPS code). Embedding
only runs in an **overnight window** (00:00–06:00 `America/Phoenix`, gated
in `embed_tick.sh`) so it doesn't contend with daytime crawler traffic —
outside the window each tick is a no-op (a manual `embed_opinions` run
bypasses the gate). A
single-flight `flock` prevents overlap; `embed_opinions` raises (non-zero
exit → NFSN emails) on any failure and rewrites a `.embed_progress`
beacon each batch. `heartbeat.sh` is now a pure alerter — stale beacon
with pending > 0 → email. No wrapper, no `.embed_expected`/`.embed_last_exit`
sentinels, no resurrect logic. See *Deployment cheat sheet* below. **Both NH
and AZ are now 100% embedded** (AZ finished across the overnight windows).

**Future feature work** is scoped in `docs/ROADMAP.md` — Phases 13-21
covering attorney extraction, citation treatment graph, holdings,
smart alerts, brief cite-checker, firm networks, opinion diff, and a
public read API. Numbering picks up where `STATE_ROLLOUT.md` (Phase
12 = weekly cron) leaves off.

**Right after this session:** see *Open work, ranked* below.

## When asked to bring a new state online

Read `docs/STATE_ROLLOUT.md` first. It's a 12-phase end-to-end runbook
distilled from the MN/NH/AZ rollouts with explicit commands and gates
for each phase. Don't improvise a new sequence — the runbook captures
the failure modes (CL API 21-hour-cooldown trap, alias-cert timing,
parser scope split, Akamai-blocked court sites) and the universal-vs-per-state
matrix.

## The repo and its shape

- Django 5.2 + PyMySQL + MariaDB 11.7 on NFSN. Local dev defaults to SQLite.
- Frontend = Django templates + minimal JS + HTMX where it earns its keep
  (only the bulk tag-review admin uses it). No SPA, no build pipeline.
- State subdomain (`mn.docketdrift.com`, ...) is resolved to a `State` row
  by `opinions/middleware.py:StateRouterMiddleware` and attached as
  `request.state`. Apex has `request.state = None`.
- Before the state router, `opinions/middleware.py:CrawlerBlockMiddleware`
  hard-blocks aggressive SEO crawlers (SemrushBot, AhrefsBot, etc.) with a
  429 BEFORE any view or DB query runs.
- Per-state landing template is `opinions/templates/opinions/state_landing.html`,
  rendered by `views.home()` when `request.state` is set. Auto-disclosure
  banner fires when the most recent opinion is > 30 days old.
- Production deploys via `git push origin main` then SSH-driven NFSN-side
  pull + `nfsn -j signal-daemon gunicorn TERM`. Onion prefers I drive the
  full deploy loop (her SSH config has the `docketdrift` alias).

## Data is sacred — query privacy is non-negotiable

This is a product principle, baked in 2026-06-25, that constrains EVERY
future build. DocketDrift's users are lawyers, and a lawyer's research
trail — what they searched, what theory they were chasing — is work
product. If we store it, it can be **discovered** (subpoenaed) in
litigation. Onion's rule: *"data is sacred."* Our edge over paid
databases is architectural, not promissory: **we cannot produce what we
never stored.**

Concrete, enforced rules:

- **Search queries never appear in a URL.** Search is **POST** (the term
  rides in the request body, never the query string), so it can't land in
  the gunicorn access log, the NFSN upstream proxy log, a CDN cache key, a
  browser history entry, or a shared/bookmarked link. Highlight-on-arrival
  on the opinion page is driven by a **URL `#fragment`** (fragments are
  never sent to the server). Do NOT add a feature that puts a user's query
  text into a GET parameter.
- **THERE IS NO ANALYTICS SCRIPT (as of 2026-08-18).** No Google, no
  goatcounter, no third-party beacon — zero visitor JS leaves the site.
  goatcounter had run with `path` pinned to a constant `"/"` and `referrer`
  blanked (region + device only). It was **removed** when a UA-rotating,
  resource-blocking scraper in a Singapore datacenter executed the beacon on
  every fetch and made the region signal read **74% Singapore** — a number
  about a bot, not readers. Lesson worth keeping: *an analytics signal a
  crawler can forge is decoration, not measurement*, and this one was
  shipping visitor data to a third party to produce it. Do NOT add an
  analytics script back without the Author's say-so.
- **Geography/device now come from our own access log, at NETWORK
  granularity.** `docketdrift_site/gunicorn_logging.py:NetworkOnlyLogger`
  adds a `{x-client-net}i` atom from `X-Forwarded-For` truncated to **/24
  (IPv4) or /48 (IPv6)** — `%(h)s` behind NFSN's proxy is only the internal
  `10.x` address, which is why the log couldn't tell reader from crawler.
  **Never widen this to the full address, not even "temporarily."** A full
  IP beside a path + timestamp is exactly the "who read this opinion?"
  artifact the subpoena test forbids; a /24 fingerprints a datacenter range
  and geolocates at country level (the only two questions we have) while
  covering hundreds of shared, dynamically-reassigned residential addresses.
  Malformed input degrades to `-`, never a raw value.
- **The gunicorn access log is query-stripped.** `run.sh` uses a custom
  `--access-logformat` that logs `%(U)s` (path only) and omits the query
  string AND the referer. Keep it that way; the default format logs the
  full request line with the query.
- **Never persist a query server-side either.** No logging the search
  term, no storing it in a session, no analytics row keyed on it. Process
  it in memory and let it go. Caches key on opinion id, never on query/user
  (see the similar-opinions cache).
- **Decouple identity from activity.** If a signed-in tier is ever built
  (see ROADMAP Phase 22 / the two-tier idea), the account proves
  *entitlement* only — put no `user_id` on anything that touches a query or
  a view. The promise is about activity, not identity.

When in doubt, the test is: *could this artifact be subpoenaed to reveal
what a user was researching?* If yes, don't create it. "Store it securely"
is not good enough — "never store it" is the bar.

## Recurring gotchas — DO NOT MAKE THESE AGAIN

### Sending email from NFSN (report-error form) — three traps, all measured 2026-08-06

`/usr/bin/sendmail` is NFSN's own wrapper (symlink to `/nfsn/sendmail`); the
`/report-error/` view pipes to it. Everything below was established with probe
emails, not guessed:

1. **Non-ASCII in a hand-built header = SILENT drop.** A raw UTF-8 em-dash in
   the Subject made the relay eat the message -- no bounce, no log, exit 0.
   Build mail with `email.message.EmailMessage` (RFC-2047-encodes headers,
   raises on CR/LF header injection); NEVER hand-join header strings.
2. **First-contact mail is GREYLISTED** (~20-40 min deferred). Do not diagnose
   deliverability from the first ten minutes -- that misread caused a wrong
   "SPF fix" here that had to be reverted.
3. **Sender choice is measured, not aesthetic:** `From: hello@docketdrift.com`
   lands in Gmail's Primary/Updates; NFSN's default sender lands in SPAM.
   Keep the hello@ From.

Also: `hello@docketdrift.com` is an NFSN Hybrid Forwarding alias ->
kellye.sundar@gmail.com (member panel; catch-all -> onionmadder@gmail.com).
Gmail's default search EXCLUDES Spam/Trash -- verify delivery with
`in:anywhere <marker>`. And git-bash curl on Windows mangles non-ASCII in
--data-urlencode: test unicode submissions from the FreeBSD side.

### Django template comments are single-line only

`{# this is fine #}` works **only on a single line**. Multi-line `{# ... #}`
renders as raw page text — Onion has caught it bleeding into the apex
hero multiple times. **Always** use `{% comment %} ... {% endcomment %}`
for any block longer than a single line.

This is now enforced by `opinions/checks.py:opinions.E001`, a deploy-blocking
Django system check that walks every `.html` under `opinions/templates/` and
the TEMPLATES["DIRS"] entries. Any multi-line `{# #}` is an `Error` — `manage.py
check` exits non-zero, `runserver` refuses to start, and the NFSN gunicorn
boot aborts. **Trust the check.** If your CI fails on `opinions.E001`, fix
the comment — don't disable the check.

```django
{# good: one-liner annotation #}
{% comment %}
good: multi-line block,
spanning several lines,
WILL NOT render to the user.
{% endcomment %}
```

### Don't use nested f-strings with bracket-indexed lookups

`f"{row['n']}"` is fine in Python 3.12+ but **NFSN runs Python 3.11**, where
nested-string brackets inside an f-string are a `SyntaxError`.
Use `%`-formatting or `.format()` for dict-key lookups in scripts that
will run on production.

### Function-local imports in a conditional branch → UnboundLocalError

A `from datetime import timedelta` (or any import/assignment) placed
**inside an `if` branch** makes that name **function-local for the entire
function** — Python decides local-vs-global at compile time. Any *other*
code path that uses the name without having executed that branch raises
`UnboundLocalError` at runtime: not at import, not at `manage.py check`,
only when that path runs. This shipped a live 500 on bare `/opinions/` —
the no-search path used `timedelta`, but it was imported only inside the
search branch, so it was unbound on the default landing. It hid because
the search path worked and the system check passed. **Import at module
scope**, or import locally in *every* branch that uses the name. Especially
watch the filtered/default and search/no-search branch splits in
`opinion_list` and `home`.

### NumPy on NFSN's FreeBSD is broken

Both numpy 2.x and 1.x ship FreeBSD wheels missing `cblas_sdot` from the
system BLAS — `import numpy` fails at runtime. **Do not add numpy as a
dependency.** The cosine math in `suggest_tags` uses MariaDB's native
`VEC_DISTANCE_COSINE` instead; the same primitive powers semantic search.

If a new task wants matrix math, lean on MariaDB VECTOR + raw SQL, or pull
small enough datasets that pure-Python loops are fine.

### Court.short_label is a Python @property

Not a database column. You CANNOT `.values("court__short_label")` or
`.annotate(...)` against it. Group by `court_id` and resolve to Court
instances in Python.

### MariaDB drops idle connections during long sleeps

Any management command that does a 30-60s `time.sleep()` between DB writes
(CL rate-limit cooldowns, Voyage API backoffs) needs retry-with-reconnect
on the write side. Pattern lives in `embed_opinions` and `ingest_court`.
Catch `OperationalError (2013, "Lost connection to MySQL server during
query")`. Use bare `BaseException` (not `Exception`) — NFSN's SSL socket
raises `KeyboardInterrupt` on EINTR during long sleeps.

Settings.py has `CONN_HEALTH_CHECKS = True` so the gunicorn-side connection
pool pings each pooled connection before reusing it. That fixed a class of
500s on judge pages under bulk-load contention.

### Defer raw_text + html_content on list-style queries

`Opinion.raw_text` and `Opinion.html_content` are TEXT columns holding
50-100KB each. Any list-style query (statute_detail, judge_detail's
recent_opinions, tag_detail, opinion_list) must `.defer("raw_text",
"html_content")` or pulling 50 rows blows past gunicorn's timeout.

Only `opinion_detail` actually renders `raw_text`.

### StatuteCitation default ordering bleeds into `.distinct()`

`StatuteCitation.Meta.ordering = ["opinion", "text_offset"]` silently joins
back to Opinion when used in a `.values_list().distinct()` chain. Always
chain explicit empty `.order_by()`:

```python
StatuteCitation.objects.filter(...).order_by().values_list("opinion_id", flat=True).distinct()
```

### Pre-resolve court IDs to skip the join

Bad (slow on 120K-row corpus):
```python
qs = Opinion.objects.filter(court__state=state)
```
Good:
```python
court_ids = list(state.courts.values_list("id", flat=True))
qs = Opinion.objects.filter(court_id__in=court_ids)
```
Court table is small (a handful of rows per state); resolving in Python
first turns a JOIN+COUNT(*) into an FK-index lookup. `opinion_list` does
this; `statute_detail` does an equivalent trick with `id__in` over a
pre-materialized list.

### Don't `.annotate(Min/Max(...))` on a queryset you also `select_related`

`Judge.objects.select_related("court").annotate(first=Min("panel_votes__opinion__release_date"))`
forces a `GROUP BY` over **every selected column** — including the
`bio_summary` TEXT field and all of `court`'s columns — across the
panel-vote join. Grouping on TEXT spools a temp table + filesort and
blows past `max_statement_time` (1969) even for a ~70-row roster. This
shipped a live 500 on `/current-judges/?era=all` (2026-06-15). Aggregate
in a SEPARATE query keyed on the FK id only, then join in Python:
```python
spans = {r["judge_id"]: (r["first_op"], r["last_op"]) for r in
    PanelVote.objects.filter(judge_id__in=ids)
        .values("judge_id")                       # GROUP BY judge_id alone
        .annotate(first_op=Min("opinion__release_date"),
                  last_op=Max("opinion__release_date"))}
```
Related: when sorting those groups in Python, keep the sort key one type
— a `default=999` (int) fallback beside a CharField `court.level` (str)
raises `TypeError: '<' not supported between 'str' and 'int'`. `current_judges`
hit both in the same change.

### `aggregate(Min/Max(release_date))` over a `court_id__in` filter scans the corpus

`Opinion.objects.filter(court_id__in=ids).aggregate(Min("release_date"), Max("release_date"))`
does NOT use the `release_date` index — under the `court_id__in` filter
MariaDB scans every matching row for the min/max. On MN's 60K-row corpus
that's 25s+ → `1969` when the cache is cold under load, which **500'd the
apex + every state landing + `/opinions/`** (the `_state_landing_stats`
bundle, 2026-06-16) and cascaded into site-wide worker saturation. Use the
indexed `ORDER BY ... LIMIT 1` instead — it walks the `release_date` index
and returns in ~20ms:
```python
first = qs.order_by("release_date").values_list("release_date", flat=True).first()
last  = qs.order_by("-release_date").values_list("release_date", flat=True).first()
```
(The per-judge variant in `judge_detail` is the same pattern but bounded to
one judge's opinions — ~2.4s worst case — so it's left as an aggregate.)

### Similar-opinions semantic search needs a date_cutoff

`VEC_DISTANCE_COSINE` over the state's full corpus is O(N) because the
embedding column allows NULL (MariaDB VECTOR INDEX requires NOT NULL).
At 60K rows the scan was fast; at 120K+ it blew past 20s and saturated
gunicorn's single worker. `semantic.similar_to_opinion` now caps the
candidate set to a 3-year window around the source opinion's
release_date. Don't remove this cap without first migrating embeddings
to NOT NULL + creating the actual VECTOR index.

### A slow cosine scan POISONS the connection pool → site-wide 500s

The same O(N) `VEC_DISTANCE_COSINE` scan (above) is more dangerous than
"just slow." On a dense state (MN/AZ, recent-year density) a cold scan can
cross the 25s `max_statement_time`; MariaDB KILLs it, and the KILL leaves
the **pooled** connection in an interrupted state. The *next* request to
reuse that connection 500s with errno **188** ("Operation was interrupted")
or **1317** on whatever it runs next — so one slow opinion page cascades
500s onto unrelated pages site-wide until `CONN_HEALTH_CHECKS` cycles the
connection out. `?q=` search-result links amplify it: the unique query
string busts the CDN cache, so every visit re-runs the scan at origin.
(This caused the 2026-06-24 burst of 500s. Crawlers are skip-listed via
`request_is_crawler`, so the triggers are real users + `?q=` traffic — and
browser-UA curl testing of MN/AZ opinion pages, which bypasses the skip.
Don't load-test the similar-opinions widget against MN/AZ from a browser UA.)

Three defenses now live in `semantic.py:_run_vector_query` + `opinion_detail`
(2026-06-24); keep all three until the VECTOR INDEX lands:
1. **Catch + drop the connection.** `_run_vector_query` wraps both cosine
   SELECTs in `except Exception` (NOT just `DatabaseError` — the KILL often
   lands during `fetchall`, surfacing as a RAW `pymysql.err.OperationalError`
   that is not a DatabaseError subclass), then `connection.close()` so the
   poisoned connection is discarded, and returns `[]` (page degrades to no
   widget instead of 500ing).
2. **Per-opinion cache.** `opinion_detail` caches the result keyed on the
   opinion id (NOT the URL), 24h, so all `?q=` variants share one scan.
3. **Self-bound the scan.** `SET STATEMENT max_statement_time=12 FOR <select>`
   caps a cold scan at 12s (vs the 25s session cap) so the single worker
   can't stall the full 25s. NH (~215ms) never reaches the bound.

The "real fix" was *supposed* to be roadmap #14 (migrate `embedding` → NOT NULL
+ VECTOR INDEX, turning the scan into a sub-100ms indexed lookup). **It was
attempted 2026-06-26 and is INFEASIBLE on NFSN's shared DB** — see the new
"MariaDB VECTOR INDEX is infeasible on NFSN shared hosting" gotcha below. So
**all three defenses above are PERMANENT, not temporary** — do NOT remove them
expecting an index to land. See the `date_cutoff` gotcha above and the
"Pooled MariaDB connection retains 'interrupted' state" gotcha below.

### MariaDB VECTOR INDEX is infeasible on NFSN shared hosting (roadmap #14 blocked)

Attempted 2026-06-26, proven impossible on this DB. Building an HNSW vector
index over our ~119K × 1024-dim embeddings needs ~488 MB of cache. NFSN's
shared `madmaster.db` gives **`mhnsw_max_cache_size` = 16 MB** (GLOBAL-only) and
**`innodb_buffer_pool_size` = 8 MB**, and our user `docketdrift_app` has only
`ALL PRIVILEGES ON docketdrift.*` — **no SUPER, so `SET GLOBAL` is denied**
(errno 1227). We cannot enlarge either. Both build paths hit the same wall:

1. **In-place `ALTER TABLE opinions_opinion ADD VECTOR INDEX`** uses
   `ALGORITHM=COPY`, which rebuilds the **entire 2.75 GB table** (the
   `raw_text`/`html_content` TEXT blobs make it huge) through the 8 MB buffer
   pool. It ran **9+ hours**, reached only stage 1→2 of 4, and was
   **UNKILLABLE mid-DDL** — `KILL`/`KILL QUERY` are ignored during the
   `copy to tmp table` / `Enabling keys` stages, and you can't restart a shared
   daemon. NEVER run this as `manage.py migrate`: the client disconnects, the
   ALTER holds a write-blocking MDL on opinions_opinion for hours (reads still
   work), and you can't stop it. It eventually self-aborted at a stage boundary.
2. **Denormalized slim table** `opinion_embedding(opinion_id, court_id,
   embedding)` + vector index, populated by incremental `INSERT … SELECT`:
   starts ~513 rows/s on an empty graph but **degrades to ~11 rows/s by 20K
   rows** as the HNSW graph overflows the 16 MB cache → hours, still slowing.

**Re-confirmed 2026-06-27 (do not attempt a third time without new infra).** A
retry first cleared the prerequisite the 2026-06-26 run blamed (19 NULL
embeddings: deleted 12 zero-`raw_text` metadata stubs, embedded 7 fresh
ingests → 0 NULL), then re-ran the in-place `ADD VECTOR INDEX`. It hit the
**identical** wall: ~47 min in `copy to tmp table`, never finished, and when the
migrate client died the server-side DDL self-aborted at a stage boundary (as
before). **The blocker was never the NULL rows — it is the COPY-rebuild
mechanics.** Data prep does not change the outcome; only new infra (below) will.

**Consequence:** the `semantic.py` band-aids (the broad-`except`+`close`, the
`SET STATEMENT max_statement_time=12` self-bound, the 3-yr `date_cutoff`, the
24h per-opinion cache) are the **permanent** mitigation. NH is fine without an
index (~215 ms, 20K rows); the unfixable pain is MN (60K) + AZ (38K).

**Out-of-band schema note (updated 2026-06-27).** On BOTH attempts the
`MODIFY … NOT NULL` step committed before the index DDL, so
`opinions_opinion.embedding` is `VECTOR(1024) NOT NULL` on prod even though
migrations sit at **0025** and nothing records it (there is no migration
0026/0027 on prod — 0026 is the locally-parked holdings work; the abandoned
VECTOR-INDEX migration was deleted, never committed). Do NOT revert NOT NULL →
NULL — that's another ~2.75 GB ALTER copy (same unkillable trap).

**BUT NOT NULL is NOT free**, contrary to the earlier "harmless" note: `embedding`
is a raw, un-modeled VECTOR column, so every Django ORM insert
(`ingest_court` `update_or_create`, `ingest_pdfs` `Opinion(...).save()`) OMITS
it → under `STRICT_TRANS_TABLES` a NOT-NULL column with no default raises
**errno 1364 "Field 'embedding' doesn't have a default value"** and **new-opinion
ingestion fails**. (Existing-row UPDATEs, incl. the overnight embed, are fine —
they set `embedding` explicitly.) Fix shipped 2026-06-27: a **zero-vector
`DEFAULT`** added via
`ALTER TABLE opinions_opinion ALTER COLUMN embedding SET DEFAULT (Vec_FromText('[0,0,…]')), ALGORITHM=INSTANT`
— a pure-metadata change (no table rewrite; returns in ms) that lets omitted-column
inserts succeed. New opinions land with a placeholder `[0,…]` vector +
`embedding_pending = TRUE`; the overnight embed replaces it with the real one.
To keep those placeholders out of cosine results, **`semantic.py` now gates both
vector queries on `embedding_pending = 0`** (replacing the now-always-true
`embedding IS NOT NULL`) — a zero vector has a degenerate cosine distance, so it
must never reach the scan. This default is also out-of-band (not in any
migration); if you ever rebuild prod from migrations, re-apply the NOT NULL +
zero-vector default by hand.

**Net effect of the 2026-06-27 retry:** no index shipped (still infeasible), but
the data is cleaner (0 NULL embeddings, 12 textless stubs deleted) and ingestion
is protected by the zero-vector default + the `embedding_pending` search gate.

**To unblock later:** a DB with real RAM + SUPER (dedicated instance, not shared
`madmaster.db`), an external vector store, or dimensionality reduction (a
256-dim embedding for ~4× cheaper scans — verify voyage-law-2 quality first,
it's not documented as Matryoshka).

### One non-covered column beside a court_id filter = 2.75GB clustered walk

Bit TWICE on 2026-08-06b, in different code, same mechanism. A query like
`WHERE court_id IN (...)` selecting ONLY columns carried by the
`(court_id, case_number)` unique index (id rides along implicitly) is
index-only — "Using index", ~0.1s. Add ONE column that index doesn't carry
(`release_date`, anything) and the optimizer abandons it for a PRIMARY-key
walk, dragging the 2.75GB clustered rows (raw_text and all) through the 8MB
buffer pool to filter by court. Measured: the sitemap chunks 27s → 0.08s
(dropped `release_date`/`<lastmod>` from the SELECT), and tag-review's
`_resolve_state_opinions` 20.6s → fast (raw SQL + FORCE INDEX; per-court
equality, not IN). Two corollaries:

- **A query that was fast can regress silently as the corpus grows** — the
  July "~300ms even for AZ" measurement was true then and 20.6s now; the
  plan flipped, not the code. Re-measure before trusting old latency notes.
- The smallest state pays the most on shared-table walks (NH's only sitemap
  chunk was the slowest), same as the FULLTEXT candidate mechanism below.

Related trap, same day: the slim embedding table has deliberately NO
secondary index, so any `WHERE opinion_id = X` there scans — and in a
DELETE, **locks** — all 128K rows (errno 1206, shared lock table). Always
address its full clustered PK `(court_id, release_date, opinion_id)`.

### FULLTEXT search on a common term must be CAPPED, never unbounded

InnoDB FULLTEXT scores EVERY matching document. A common term ("negligence"
→ 15K of 60K MN opinions) makes an unbounded `MATCH ... AGAINST` combined with
`COUNT(*)` + `ORDER BY release_date` run **20-25s** (measured), blow the 25s
`max_statement_time`, get KILLed, and **poison the pooled connection** — the
same site-wide-500 cascade as the cosine gotcha above. This shipped a live
trap on `opinion_list` search (found + fixed 2026-06-24/25).

Fix in `opinion_list` + `_fulltext_candidate_ids` (`views.py`): pull a capped
set of candidate ids straight from the fulltext index with a **`LIMIT`** —
that lets InnoDB stop early (~1.7s at cap 200 vs 24s unbounded). Then
count/sort/paginate over the bounded id set. Hard-won specifics:
- **Only `court_id` may sit beside `MATCH`** in the candidate query. A
  `release_date` range there makes the optimizer drop the fulltext index and
  re-times-out — apply the date window AFTER, on the bounded PK set.
- **Self-bind + close on failure** (`SET STATEMENT max_statement_time=12` +
  `except BaseException: connection.close()`), same defense as
  `semantic._run_vector_query`, so a pathological term degrades, never poisons.
- **When capped (over-broad), SKIP the semantic cosine + the per-row snippet
  INSTR** — otherwise each stacks multi-second bounds onto the single worker's
  4 threads and saturates them. A capped term costs just the one bounded
  candidate fetch; it's labeled "200+ — narrow your search" and shown
  date-sorted WITHOUT the date window (the sample is fulltext-index order, not
  newest-first, so windowing it would mislead).
- Normal (non-broad) searches are unchanged: exact count, newest-first,
  semantic + snippets intact. NORMAL-search latency (~5-7s on MN) is the
  pre-existing semantic O(N) cosine scan — roadmap #14 (VECTOR INDEX) is the
  fix; it's bounded + non-poisoning today.

### `.values("pk")` alone doesn't strip select_related from COUNT

Django's `QuerySet.count()` clones the underlying Query. On some
queryset shapes that clone preserves both the `.select_related` JOINs
AND the `ORDER BY` clause even though neither affects the count. Using
`.values("pk")` strips the SELECT field list but Django can keep the
JOIN if the queryset's Query object was built with select_related
state.

The hard fix is the chain in `opinions/paginators.py:NoJoinCountPaginator`:
```python
@cached_property
def count(self):
    cleaner = self.object_list.select_related(None).order_by()
    return cleaner.values("pk").count()
```
`select_related(None)` is the explicit reset; `order_by()` (no args)
strips the ORDER BY so the count doesn't pointlessly sort before
aggregating. Filter clauses including raw `.extra()` SQL (FULLTEXT
MATCH()) are preserved.

Used in: OpinionAdmin changelist, `opinion_list` public search,
`tag_detail` paginator. Any future paginator over a select_related'd
queryset on a big table should reach for this paginator.

### Decorator-orphan SyntaxError on helper insertion

When adding a helper function or module-level constants BETWEEN an
existing `@cache_control(...)` decorator and the view it decorates,
the decorator silently attaches to the helper, which crashes module
load with `SyntaxError: invalid syntax` (decorator on a variable
assignment) and 500s the entire site at every URL.

I hit this twice in the 2026-06-09 session — once on `judge_detail`,
once on `opinion_list` — because the editor pattern of "find the
function header, insert helper just above it" lands inside the
decorator/function pair. Move the new helper ABOVE the decorator, or
move the decorator down to immediately precede the view's `def`.

### Multi-line `{# #}` template comments — opinions.E001

Already documented above, but worth re-stating: **I broke this myself**
in the 2026-06-09 session by adding a 3-line `{# ... #}` block in
`opinion_detail.html`. The E001 check is deploy-blocking, so any
`manage.py` invocation (including `embed_opinions`) failed during
Django setup. The self-respawning embed wrapper happily logged
"Restarting in 30s" for ~48 hours while the embed never advanced.

Lessons restated:
- Use `{% comment %}{% endcomment %}` for any comment that doesn't
  fit on a single line.
- When a long-running wrapped command stops advancing for unexpected
  reasons, the first thing to check is whether the most recent commit
  introduced a system-check failure. Run `python manage.py check` on
  NFSN if in doubt.

### Pooled MariaDB connection retains "interrupted" state after KILL QUERY

After running `KILL QUERY <id>` against a stuck query, the next request
that picks up the same pooled connection from gunicorn's worker can
hit `pymysql.err.OperationalError: (1317, 'Query execution was
interrupted')` even though nothing's actively interrupted. The bad
connection stays in the pool until `CONN_MAX_AGE` (60s) expires.
During that window every request lands a 500.

If you find yourself debugging stuck queries on prod, prefer
`nfsn -j signal-daemon gunicorn TERM` over `KILL QUERY` — the worker
restart flushes the pool clean rather than leaving poisoned
connections behind.

### NFSN proxy cache can serve stale 503 for minutes

After fixing a slow/broken endpoint and restarting gunicorn, the public URL
may still return cached 503 for a few minutes. Bypass with the internal
gunicorn address from `/home/logs/daemon_gunicorn.log` (`Listening at: ...`)
to confirm the fix is real:

```bash
ssh docketdrift 'curl -sS -H "Host: mn.docketdrift.com" http://10.0.175.75:8000/some/path/'
```

### NH/AZ-COA court sites are Akamai-blocked

- `courts.nh.gov` returns 403 to any non-residential IP for every path,
  including sitemap.xml. WebFetch and curl from server-side both fail.
- `coa1.azcourts.gov` is a DotNetNuke site, more complex than the AZ
  Supreme single-page roster.
- `appeals2.az.gov` is yet another DNN host, different paths.

Workaround: a REAL browser on Onion's residential Windows box. **NOTE
(2026-06-15): a residential IP alone is NOT enough** — Akamai fingerprints
the client, so `curl` / `requests` / even Playwright's `request` API all
403 from the residential IP too. What gets through is Playwright driving the
installed Chrome (`channel="chrome"`, headed); images must be pulled with an
in-page same-origin `fetch` (the request API is fingerprinted as well). The
NH Supreme **justice roster** is scraped this way by
`scripts/nh_scraper/scrape_nh_justices.py` → `scripts/fetch_judge_photos.py`
→ `localize_judge_photos` (see the judge-photo pipeline). NH **opinions**
are fetched the same way by `scripts/nh_scraper/scrape_nh_opinions.py` →
`ingest_pdfs --state NH --court supreme`. AZ-COA judge bios are still TODO (#41).

### `embedding IS NULL` is unindexable; use the `embedding_pending` shadow column

`Opinion.embedding` is a raw-SQL VECTOR column (added by migration,
not declared as a Django field). MariaDB cannot index NULL-ness on
that column, so the embed_opinions hot-loop SELECT
`WHERE embedding IS NULL ...` does a full-table scan on every batch.
At low embedded-coverage on a new state (e.g. AZ at 1-5%) that scan
takes 25-30 seconds per batch -- long enough that NFSN's wallclock
supervisor culls the wrapper before it makes meaningful progress.

The 2026-06-14 fix in migration 0023 adds an indexed
`embedding_pending` BooleanField that shadows the same state. The
composite index on `(embedding_pending, court_id)` makes the batch
fetch sub-100ms regardless of corpus size. `embed_opinions` flips the
flag in the same UPDATE statement that writes the vector, so the two
fields stay consistent without a trigger.

If you ever need to migrate the `embedding` VECTOR column itself
(e.g. NOT NULL constraint for VECTOR INDEX) **set max_statement_time
to 0 inside the migration first** (see next gotcha) -- otherwise the
ALTER will run past the 25s cap.

### `case_number` is NOT unique — a docket follows a case through review

A case keeps its docket number when it's appealed, so the SAME `case_number`
carries the Court of Appeals opinion AND the later Supreme Court opinion.
In MN that's **1,292 case_numbers with two opinions each** (only 32 are true
same-court duplicates). They are DISTINCT opinions — never "dedupe" them by
deleting one; you'd be deleting real law.

Consequences, both hit live in 2026-07:
- `Opinion.objects.get(case_number=...)` raises `MultipleObjectsReturned` and
  **500s the page**. Use `_pick_opinion()` in `views.py` (highest court wins,
  stable id tiebreak) — never a bare `.get()` on case_number.
- `/opinion/<case_number>/` can only show ONE of the pair, so the other has no
  URL unless you disambiguate. `?court=appeals|supreme` selects explicitly and
  the detail page renders an "Also decided in this case" link. Any new
  case_number lookup (or a future per-court URL scheme) has to honor this.

### Batch commands + report scripts MUST lift max_statement_time

`settings.py` puts `SET SESSION max_statement_time = 25` on EVERY connection --
right for web requests, wrong for batch work, and management commands inherit
it. Anything doing a corpus-scale scan, GROUP BY, or big read gets killed with
errno **1969** (or the connection is dropped outright, errno **2013**).
This bit `suggest_tags`, `corpus_insights`, `load_reporter_cites`, and two
throwaway export scripts in a single session. Standard opener for any batch
command:

```python
if connection.vendor == "mysql":
    with connection.cursor() as cur:
        cur.execute("SET SESSION max_statement_time = 0")
```

Also: don't materialize a huge result in one query on the shared DB — a single
`list(qs)` over ~98K rows got dropped (2013) even with the cap lifted. Batch it
(PK-windowed or chunked `__in`) with retry-and-reconnect, per
`load_reporter_cites` / `backfill_reporter_cite`.

### Long migrations trip max_statement_time = 25

`settings.py` sets `init_command: "SET SESSION max_statement_time =
25"` on every MariaDB connection. That cap is right for web requests
but too tight for any migration that touches a 240K-row opinions_opinion
table -- ALTER TABLE + CREATE INDEX both ran ~30s on 2026-06-14 and
got killed mid-statement with errno 1317 ("Query execution was
interrupted"), leaving the schema half-applied.

Fix in the migration itself, not by editing settings:

```python
operations = [
    migrations.RunSQL(
        "SET SESSION max_statement_time = 0",
        reverse_sql=migrations.RunSQL.noop,
    ),
    # ... schema ops ...
]
```

The SET only affects the migration's connection, so web traffic
continues to have the 25s ceiling. Migration 0023 ships this pattern.

### gunicorn worker recycle every ~3 minutes -> cyclical user-visible slowness

`run.sh` originally had `--max-requests 200 --max-requests-jitter
50`. With `--workers 1` and any sustained traffic (crawlers +
heartbeat + precompute cron + real users), the single worker hit
recycle every 150-250 requests -- which on the live site landed at
every 2-5 minutes. Each recycle, the new worker pays cold-DB-
connection + cold-FileBasedCache + cold-template-compile cost on its
first batch of requests, and that batch stalls 5-20 seconds. Users
hit it as "sometimes the page loads instantly, sometimes it hangs for
15-20 seconds" cycles every few minutes.

Fixed 2026-06-14 by bumping to `--max-requests 5000 --max-requests-
jitter 500` (25x). Django doesn't actually leak meaningful memory in
normal operation; the original 200 was paying for a problem we don't
have. A typical recycle now happens every ~75-90 minutes during
sustained traffic, basically invisible to real users.

Diagnose by grepping for `Worker exiting` in
`/home/logs/daemon_gunicorn.log`. If the timestamps are < 10 min
apart under any sustained load, max-requests is too aggressive.

### NFSN's ~10-minute wallclock cull on shared-hosting daemons

NFSN's shared-hosting supervisor quietly SIGKILLs long-running
daemon-style processes after roughly 10 minutes. SIGKILL is silent
(no log, trap-EXIT doesn't fire). This is policy, not load-dependent.

**Don't fight the cull — don't run daemons.** The old approach (a
self-respawning wrapper + heartbeat-resurrect + `99`-sentinel
handshake) tried to survive the cull and bred a whole class of
silent-death bugs (exit-0-on-failure, duration-based brake blind spots,
missing-sentinel stand-down). It was removed on 2026-06-15.

The replacement: `embed_opinions --max-runtime 480` makes each run
SELF-EXIT cleanly well under the cull, and an NFSN scheduled task
(`scripts/embed_tick.sh`, every ~10 min) just keeps invoking it.
A killed pass is now harmless — the `.embed_progress` beacon is written
every batch, the `flock` releases on death, and the next tick resumes.
If you ever need another long-running job on NFSN, use this pattern
(bounded command + scheduled task), NOT a resident daemon.

### Local SSH-jail egress to public docketdrift.com is flaky

Curls from inside `ssh docketdrift '...'` to `https://docketdrift.com/`
sometimes time out due to NFSN's intra-rack routing for outbound HTTPS to
the same hostname. Use the internal gunicorn address (`http://10.0.175.75:8000`)
with `-H "Host: mn.docketdrift.com"`, OR ask Onion to refresh her
browser, OR check the daemon access log to confirm real traffic is landing.

## Deployment cheat sheet

```bash
# Pull + restart (code + middleware + settings)
ssh docketdrift 'cd /home/private/docketdrift && git pull && nfsn -j signal-daemon gunicorn TERM'

# Pull + migrate + restart (new migration)
ssh docketdrift 'cd /home/private/docketdrift && git pull && source .venv/bin/activate && python manage.py migrate && nfsn -j signal-daemon gunicorn TERM'

# Pull + collectstatic + restart (static asset changes)
ssh docketdrift 'cd /home/private/docketdrift && git pull && source .venv/bin/activate && python manage.py collectstatic --noinput && nfsn -j signal-daemon gunicorn TERM'

# Tail daemon log
ssh docketdrift 'tail -f /home/logs/daemon_gunicorn.log'

# Run a system check after a deploy (catches opinions.E001 et al.
# BEFORE the wrapped long-running commands trip on it silently)
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && python manage.py check'

# Show MariaDB processlist (debug slow queries)
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && python -c "
import django, os
os.environ.setdefault(\"DJANGO_SETTINGS_MODULE\", \"docketdrift_site.settings\")
django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute(\"SHOW PROCESSLIST\")
    for r in c.fetchall():
        print(r[4], r[5], (r[7] or \"\")[:80])
"'

# EMBED PIPELINE (cron-tick model, 2026-06-15). No daemon, no wrapper.
# An NFSN scheduled task runs scripts/embed_tick.sh every ~10 min; it
# reads the target state from .embed_state and runs ONE bounded pass.
# Embedding only runs OVERNIGHT (00:00-06:00 America/Phoenix, gated in
# embed_tick.sh via EMBED_START_HOUR/EMBED_END_HOUR) so it doesn't contend
# with daytime crawler traffic; outside the window each tick is a no-op.
# To widen/move the window, edit those two constants (EMBED_END_HOUR=24 =
# all day) -- AND the matching EMBED_START_HOUR/EMBED_END_HOUR in
# scripts/heartbeat.sh, which gates its stall check to the same window so it
# doesn't false-alert on the (expected) stale beacon while embedding is
# paused. A manual run (below) bypasses the window entirely.

# Start / switch the embedding target state (takes effect next tick):
ssh docketdrift 'echo AZ > /home/private/docketdrift/.embed_state'

# Pause embedding after the current pass (scheduled task stays registered;
# it no-ops while .embed_state is absent). To resume, set .embed_state again.
ssh docketdrift 'rm -f /home/private/docketdrift/.embed_state'

# Run a pass by hand (e.g. to watch it, or to push a state to completion
# now). --max-runtime 0 = run until done; omit it to use the 0 default.
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate &&
  python manage.py embed_opinions --state AZ --max-runtime 0'

# Check progress: the beacon is "<unix_ts> <pending_remaining>".
ssh docketdrift 'cd /home/private/docketdrift &&
  echo "beacon: $(cat .embed_progress 2>/dev/null)";
  tail -3 /home/logs/embed_opinions.log'

# Is a pass running right now? (None between ticks is NORMAL.)
ssh docketdrift 'ps -axww | grep -E "embed_tick|manage.py embed_opinions" | grep -v grep || echo "(idle between ticks)"'

# REGISTER THE SCHEDULED TASK (one-time, NFSN member UI -- not scriptable):
#   Manage Site -> Scheduled Tasks -> Add:
#     Tag:      embed-tick
#     Command:  /home/private/docketdrift/scripts/embed_tick.sh
#     Schedule: every 10 minutes
# The heartbeat (separate existing task) alerts if the beacon goes stale
# with pending > 0 -- that's the only embed supervision now.
```

## Management commands reference

| Command | Purpose | State-aware? | Idempotent? |
|---|---|---|---|
| `migrate` | Standard Django migrations | global | yes |
| `ingest_court <cl_id> [--since YYYY-MM-DD]` | Pull recent opinions via CL REST API | per court | yes |
| `cron-ingest.sh` (`/home/private/docketdrift/cron-ingest.sh`) | Weekly wrapper; auto-discovers live courts via `Court.objects.filter(state__is_live=True)` | auto | yes |
| `load_cl_bulk --subset-dir <dir> --state <CODE>` | Load filtered CL bulk-dump CSVs | yes | yes |
| `scripts/cl_bulk_filter.py --state <CODE>` | Filter local CL bulk dump (~50GB sweep) to one state | yes | yes |
| `ingest_pdfs --dir <path> --state <CODE> --court <slug>` | Bulk-ingest a directory of opinion PDFs. Uses the state's registered parser to populate fields; SHA-256 dedup; optional `--no-pdf`; `--dry-run` for preview. **Used for Akamai-blocked states where CL lags.** | per state | yes (skips existing on `(court, case_number)`) |
| `embed_opinions [--state <CODE>] [--limit N] [--max-runtime N]` | Voyage embeddings on raw_text → VECTOR column. `--state` restricts to one state's courts; `--max-runtime` self-limits (cron tick passes 480). | per state (optional) | yes (`WHERE embedding_pending = TRUE`) |
| `embed_tags [--force]` | Voyage embeddings of Tag.label+description | global | yes (`embedded_at` skip) |
| `suggest_tags [--rescore-all] [--limit N]` | Score opinions vs tags via VEC_DISTANCE_COSINE | global | yes |
| `extract_statutes [--state <CODE>] [--force]` | Pull statute citations. Now multi-state via the `opinions/parsing/statutes.py` dispatcher (MN: `Minn. Stat.`, NH: `RSA`, AZ: `A.R.S.`). | per state (optional) | yes |
| `extract_citations [--state <CODE>]` | Build the `OpinionCitation` graph: parse opinion bodies for cites to other opinions, resolve against `reporter_cite`, classify treatment (`parsing/citations*.py` + `parsing/treatment.py`). **NH-only** (neutral cites); MN/AZ await a reporter-cite backfill. Batched + retry-reconnect. | per state (optional) | yes (rebuilds each opinion's edges) |
| `backfill_reporter_cite [--state <CODE>]` | Populate `Opinion.reporter_cite` from the state parser (NH neutral cites). Idempotent (fills empty only); batched + retry-reconnect. | per state (optional) | yes |
| `resolve_judges --state <CODE> [--create-missing] [--since] [--max-runtime N] [--min-id N] [--id-batch N]` | Match byline+panel to existing Judge rows; `--create-missing` mints new ones. Hybrid: state parser fills what it knows, generic byline extractor fills the rest. Carries `_CROSS_COURT_JUSTICES` (SCOTUS-surname stoplist) on the weak footer path only — see the 2026-07-27 block. **NFSN chunking:** `--max-runtime 35 --id-batch 6000` self-exits under the ~40s CPU cull. | per state | yes |
| `merge_hyphenated_judges [--apply]` | Fold PDF line-break artifact judge rows ("Struck-meyer" → "Struckmeyer") into their clean twin, only when de-hyphenating matches an existing same-state surname. **Dry-run by default.** | per state | yes |
| `merge_duplicate_judges [--apply]` | Fold exact-name duplicate rows + bare-surname rows shadowing their full-name row, per surname group, only when the surname has one unambiguous full name in that state. Conflicting first names reported + skipped. Metadata-rich row wins. **Dry-run by default.** | per state | yes |
| `extract_holdings_text [--state <CODE>]` | Populate `holding_summary` by quoting the court's OWN holding sentence VERBATIM (`parsing/holdings.py`). NOT an LLM; `holding_model` records the extractor. | per state (optional) | yes |
| `load_reporter_cites --csv <path>` | Fill `Opinion.reporter_cite` from CL's bulk citations export, matched on `courtlistener_id` (= CL cluster_id). Fills EMPTY only, so parser-derived NH cites survive. Zero API calls. | global | yes |
| `load_citation_edges --csv <path>` | Build the `OpinionCitation` graph from CL's bulk citation-map export (keyed on CL *opinion* ids → cluster_id → our Opinion). Internal edges only. No treatment/context (bulk data carries neither). Zero API calls. | global | yes |
| `cluster_citations [--state <CODE>]` | Group the verbatim citing passages behind "How this document has been cited" (Scholar-style collapse). **Data-starved outside the NH neutral-cite era** — see the 2026-07-24→25 block. | per state (optional) | yes |
| `embed_citations` | Voyage embeddings of citation context quotes, for the clustering above. | global | yes |
| `precompute_explore_tags` | Warm the explore-tags context-processor cache. | global | yes |
| `ai_citation_profile [--days N]` | Read-only report joining access-log opinion fetches to DB metadata: what KIND of law live AI agents ground on. Privacy-clean (the log is query-stripped). | global | n/a |
| `corpus_insights` | Read-only report: disposition mix, caseload trend, most-cited, hot statutes. Publishable content. | global | n/a |
| `scrape_judges <state> [--dry-run]` | Scrape current-roster bios. Supports `mn` (mncourts.gov sitemap) + `az` (azcourts.gov single page). NH is Akamai-blocked → scraped off-platform by `scripts/nh_scraper/` (residential Playwright) instead. AZ-COA still TODO (#41). | per state | yes |
| `localize_judge_photos [--dry-run]` | Repoint every judge's `photo_url` to a SELF-HOSTED `/static/opinions/judges/` portrait (no hotlinks to court sites that could go down) + apply scraped NH bios, from the committed `opinions/data/judge_localization.json` manifest. Run after `collectstatic` + restart. Portraits are downloaded locally by `scripts/fetch_judge_photos.py`. | global | yes |
| `reconcile_az_judges [--dry-run]` | One-shot merge of duplicate AZ Judge rows from the first scrape_judges run | AZ-specific | yes (no-op after merge) |
| `backfill_dispositions` | Parse dispositions from raw_text into `disposition` field | global | yes |
| `check_freshness [--today YYYY-MM-DD]` | Per-state ingest freshness monitor. Non-zero exit (NFSN-emailed) when any live state's newest opinion exceeds its staleness threshold (MN/AZ 45d, NH 60d). Wrapper `scripts/freshness_check.sh` runs weekly via NFSN scheduled task (register in member panel). Uses indexed `ORDER BY release_date LIMIT 1`, not aggregate Max. | global | yes |
| `manage.py check` | Django system checks (incl. opinions.E001 multi-line `{# #}` guard) | global | n/a |

## Architecture: where ML appears (and where it doesn't)

The site is **explicitly not** an AI legal assistant. The About page +
`/how-we-differ/` carry this in copy; the architecture mirrors it.

**ML appears in exactly two places:**

1. **Voyage embeddings for semantic search.** `voyage-law-2`, 1024-dim,
   stored in `Opinion.embedding` (native MariaDB VECTOR column). We
   compare a query vector to opinion vectors with cosine similarity
   and return a ranked list of opinion IDs. No text is generated.
2. **Tag-suggestion candidates.** Embeddings rank tags against opinions.
   Above `TAG_SUGGESTION_AUTO_APPLY_THRESHOLD` (0.40) the tag is
   auto-applied AND marked `AUTO_APPLIED` for transparent audit; below
   it the suggestion surfaces in the HTMX review queue at
   `/admin/opinions/tag-review/` for human accept/reject.

**Everything else is deterministic regex extraction.** Case number,
release date, disposition, panel composition, statute citations, court
breakdown, judge dossiers — all from `opinions/parsing/` or
`opinions/management/commands/`. No LLM is ever asked to synthesize,
summarize, or describe anything.

If a new task wants generative content (summaries, drafted text), STOP
and discuss with Onion first. The "no generation" posture is product
strategy, not engineering preference.

## Semantic color key (the deliberate palette, 2026-06-27)

The site-wide outcome palette is a deliberate, top-down semantic key
(Onion-approved), defined ONCE as CSS custom properties in
`docketdrift.css:root` (the `--dd-*` block at the top of the file) and
applied to EVERY label family so a color means the same thing
everywhere. Five anchors + one neutral:

| Hue | `--dd-` var | Meaning | Dispositions | Review status | Treatment |
|---|---|---|---|---|---|
| green `#8AFF00` | `--dd-green` | stands / blessed / followed | affirmed, modified | reviewed | followed |
| cyan `#10FEE2` | `--dd-cyan` | in motion / neutral / ML-processed | remanded, granted | processed (ai_only) | distinguished |
| pink `#FE14BB` | `--dd-pink` | overturned / reversed | reversed | — | overruled |
| magenta `#C401DB` | `--dd-magenta` | nullified harder | vacated | flagged | criticized |
| violet `#6715FF` | `--dd-violet` | terminal / explained-away | dismissed, denied | — | explained |
| neutral `#8a7e62` | `--dd-neutral` | unbucketed / merely cited | other | — | cited (default) |

Compound dispositions (`disposition_bucket == "mixed"`, e.g. "Affirmed
in part, reversed in part") render as a **diagonal green/pink split**.
The `granted`/`denied`/`modified`/`other` mappings are extensions of the
five Onion-approved buckets, chosen via the same through-line (granted =
review proceeds → cyan; denied = review ends → violet; modified = stands
as modified → green).

Mechanics worth knowing before you touch it:

- **Re-point, don't rename.** The live classes are unchanged
  (`.case-status.disposition-<bucket>`, `.review-pill--<status>` /
  `.review-dot--<status>`, `.treatment-<treatment>`, `.judge-bar-fill--<bucket>`).
  Part 1 was CSS-only — no template/Python edits — by re-pointing those
  existing rules at the `--dd-*` vars. Markup and `disposition_bucket`
  filter semantics are untouched.
- **Each hue ships `-rgb` (channel triplet for tints/glows) and `-ink`.**
  green/cyan/pink read fine as small text on `#050505`; magenta/violet/
  neutral are too dark, so `-ink` is a lightened WCAG-AA text color while
  the saturated anchor stays the border/dot/bar fill. Use `-ink` for text
  on dark, the anchor for accents/fills.
- These five anchors are intentionally DISTINCT from the `--neon-*`
  chrome tokens in `core.css` — chrome carries the brand, this key
  carries meaning. The vote-chip / concordance palette (who-voted-what)
  is a separate taxonomy and deliberately left on `--neon-*`.

## Spelling convention: American English

US litigant, US federal audience → American spellings throughout
templates, Python, and docs (color not colour, judgment not judgement,
gray not grey, analyze not analyse, toward not towards, backward not
backwards). The corpus text itself (opinion `raw_text`) and vendored
third-party content are left verbatim. Note "forwards" as a *verb*
("the proxy forwards requests") is correct American usage — only the
adverb "forwards" → "forward".

## Open work, ranked

> **SUPERSEDED — read `docs/TODO.md` instead.** This section is a frozen
> 2026-06-12 snapshot, kept because its *rationale* (why an item mattered, what
> the failure mode was) is still useful. Its priorities are not current, and
> several items listed here as open have shipped: the state-router middleware
> cache (#16), the MN COA scraper (#19), the freshness-check task (#20), NH
> dispositions, the AZ parser, and holdings are all DONE. Do not pick work off
> this list without checking TODO.md first.

State of play at session-end 2026-06-12. Items struck through were
closed in the 2026-06-09 → 2026-06-12 session.

### Priority 1 — close NH/AZ gaps

1. ~~**Finish NH embed.**~~ ✅ Done 2026-06-14 — NH at 100%, ~$2 in
   Voyage cost. Wrapper exited 0 cleanly.
2. ~~**Finish AZ embed.**~~ ✅ Done 2026-06-23 — AZ at **100%** (38,071
   embedded). The `embed-tick` scheduled task is registered and runs in the
   overnight window (00:00–06:00 Phoenix); AZ finished across those windows.
3. ~~**Run `extract_statutes --state AZ`**~~ ✅ Done 2026-06-26 — AZ A.R.S.
   citation graph went 0 → **117,045 cites** across 20,677 opinions (54.3%),
   38K-opinion sweep in 4.2 min. Required two robustness fixes to the command
   first (it had only ever run on MN): lift `max_statement_time` for the
   corpus-wide COUNT, and replace the single streaming `qs.iterator()` (NFSN
   dropped its connection mid-read, errno 2013) with pk-windowed batching +
   retry-reconnect (idempotent/resumable). `extract_statutes` is now robust for
   any large state.
4. ~~Fix AZ Supreme Court byline format (#44).~~ ✅ Done — `JUSTICES X,
   Y, and Z` plural-prefix handling shipped in commit `986e14f`. AZ
   panel votes 40 → 142.
5. ~~Re-run `resolve_judges --state AZ --create-missing`.~~ ✅ Done —
   ran with the new byline regex; AZ judge count 133 → 139.
6. ~~Fix Cruz's full_name.~~ ✅ Done — manually updated to "Maria Elena
   Cruz" in admin.
7. ~~Generalize `extract_statutes` to NH (RSA) + AZ (A.R.S.) (#43).~~
   ✅ Done — `opinions/parsing/statutes.py` is now a state-dispatched
   registry with one module per state. NH statute graph went from 0 to
   79,384 cites; AZ extractor exists but hasn't been swept yet (item 3
   above).
8. ~~NH dissent detection in `resolve_judges`.~~ ✅ Done — the generic
   byline extractor now parses `<NAME>, J., dissented.` footers (the
   convention NH uses to record solo dissenters). Concordance section
   on `/compare/judges/` can now show actual split decisions on NH
   pairs.

### Priority 2 — close coverage gaps

9. **Playwright-on-Windows scrapers** (#41) for the Akamai-blocked sites:
   - NH Supreme **judges** (`courts.nh.gov`) — ✅ DONE 2026-06-15 via
     `scripts/nh_scraper/scrape_nh_justices.py` (Playwright + real Chrome,
     `channel="chrome"`). The 5 seated justices now have official bios +
     appointment dates + self-hosted portraits.
   - NH Supreme **opinions** (`courts.nh.gov`) — ✅ scraper DONE 2026-06-15,
     **now SCHEDULED 2026-06-28** via `scripts/nh_scraper/run_nh_weekly.ps1`
     (scrape → scp → `ingest_pdfs --state NH --court supreme`), Windows Task
     Scheduler "DocketDrift NH weekly scraper" (Sun 17:00, run-only-when-logged-on).
     NH corpus is genuinely current (latest 2026-06-11 — verified the court has
     published nothing newer, not a stalled pipeline).
   - AZ COA Div 1 (`coa1.azcourts.gov`) + Div 2 (`appeals2.az.gov`) judge
     bios — still TODO (DNN hosts).

19. **MN COA coverage gap — direct-from-mn.gov scraper** (NEW 2026-06-28).
    CourtListener doesn't carry recent MN COA nonprecedential/order opinions
    (the Rickmyer A25-0969 miss). Fix = a residential-Playwright scraper like
    NH's against mn.gov's bot-walled archive → `ingest_pdfs --state MN --court
    appeals`. Recon + feasibility DONE; scraper not yet written. See the
    START-HERE block up top + `docs/MN_COA_BACKFILL.md`.
20. **Register the `freshness-check` NFSN scheduled task** (NEW 2026-06-28).
    Code shipped (`92516f6`); just needs the one-time member-panel registration
    to go live. See the START-HERE block.
10. ~~**Backfill `reporter_cite` field on Opinion.**~~ ✅ Done 2026-06-16
    (citation engine Phase 16) — `reporter_cite` populated for NH (140 neutral
    cites), paste-a-cite search live, plus the full `OpinionCitation` graph +
    treatment panel (Phase 14). **NEXT UP: roll the citation engine out to
    MN/AZ.** Blocked on a CourtListener reporter-cite backfill (MN/AZ cites are
    reporter-assigned post-publication, not in our opinion text); then add a
    per-state `citations_<mn|az>.py` extractor + run `extract_citations`. This
    is the headline next feature — see the citation-engine note up top.

### Priority 3 — editorial throughput

11. **Triage MN tag suggestions** (#39). 20,161 pending in
    `/admin/opinions/tag-review/`. HTMX UX designed for 100+/session.
    Each review recalibrates the precision/recall knee for the
    `suggest_tags` thresholds. (Onion's manual work, not a coding task.)
12. **Re-run `suggest_tags` after NH embed completes.** NH's
    tag-suggestion queue is currently sparse because most NH opinions
    weren't embedded yet; once embedding finishes, run
    `python manage.py suggest_tags` to fill the queue.
13. **Phase 1D LLM holding extraction.** Claude Haiku decomposes each
    opinion into `OpinionHolding` rows (statute_cited +
    holding_direction + holding_text). One-time ~$90 batch. Unlocks
    per-holding semantic search + per-issue judge voting. **Spend
    confirmation required from Onion before running.**

### Priority 4 — hardening / polish

14. ~~**VECTOR INDEX migration**~~ **BLOCKED (infra, not code) — attempted
    2026-06-26 AND re-attempted 2026-06-27, infeasible on NFSN's shared DB.**
    Building the HNSW index needs ~488 MB cache; we have a 16 MB GLOBAL
    `mhnsw_max_cache_size` + 8 MB buffer pool and no SUPER to raise them. The
    in-place `ADD VECTOR INDEX` is an unkillable multi-hour copy of the 2.75 GB
    table; the denormalized slim-table build degrades to ~11 rows/s as the graph
    overflows the cache. The 2026-06-27 retry first cleared all NULL embeddings
    (the thing the prior run blamed) and STILL hit the identical COPY-rebuild
    wall — proving the blocker is the rebuild mechanics, not the data. The
    `embedding` column is NOT NULL out of band; that broke ORM ingestion (errno
    1364) until a zero-vector INSTANT `DEFAULT` + an `embedding_pending` search
    gate were added (both 2026-06-27, out of band). The `semantic.py` band-aids
    are PERMANENT. See the "MariaDB VECTOR INDEX is infeasible on NFSN shared
    hosting" gotcha for the full writeup + the later-unblock paths (dedicated DB
    w/ SUPER, external vector store, or dim-reduction). **Do not attempt a third
    time without one of those infra changes.**
15. **Search-snippet INSTR query is slow on big raw_text.** The
    `_attach_match_snippets` helper in `views.py` runs
    `INSTR(LOWER(raw_text), LOWER(?))` on each result row -- across 50
    rows it can take 5-10s under embed contention. Worth caching the
    INSTR position via a session variable or switching to MariaDB's
    MATCH SNIPPET if performance becomes a complaint.
16. **State-router middleware lookup cache** (P0-2). `_resolve_state`
    hits the DB on every request. Cache by Host header for the
    duration of a worker's lifetime.
17. **Cloudflare in front of NFSN** (P0-6). Deferred because NFSN
    doesn't allow nameserver changes — needs alternate registrar setup.
18. ~~FAQ schema on About.~~ ✅ Done in the SEO + schema.org pass
    (commit `d487247`), which also added Organization +
    GovernmentOrganization markup on judge/opinion/statute pages,
    refreshed canonical URLs, expanded sitemaps, and pulled meta-
    descriptions out of the templates.

## When to ASK

- Adding NEW dependencies — flag the FreeBSD risk before installing.
  Specifically: numpy, scipy, pandas, anything with C extensions.
- Touching `settings.py` middleware order or DB config — restart is
  required; confirm the timing matches what's in flight.
- Spending money: any LLM-extraction batch job (Claude, GPT) — show the
  cost estimate before kicking off. Voyage embedding is cheap (~$0.12/M
  tokens) but still flag costs > a few dollars.
- Bringing a new state online — go through `docs/STATE_ROLLOUT.md`
  phase-by-phase, not improvised.
- Anything that produces generative legal text — full stop, this would
  break the product's anti-hallucination posture.

## When NOT to ask

- Running idempotent management commands again — safe by design.
- Fixing template comment bleed — just fix it; system check will block
  the deploy anyway.
- Restarting gunicorn after a deploy — that's expected.
- Re-running `resolve_judges` or `extract_statutes` — idempotent.
- Re-running the bulk filter or load_cl_bulk — idempotent.

## Key files added or substantially changed this session

For orientation when looking at the repo with fresh eyes. Items
added or meaningfully changed in the **2026-06-09 → 2026-06-15**
sessions are listed here — see the prior CLAUDE.md if you need the
2026-06-08 cut.

### Stability / supervisor (2026-06-14 sub-session)

- `scripts/embed_state_loop.sh` — **DELETED 2026-06-15.** Was the
  self-respawning daemon wrapper; replaced by the cron-tick model
  (`scripts/embed_tick.sh`). Its rapid-fail brake / `99`-sentinel /
  `--skip-preflight` machinery bred the silent-death bug class and is
  gone. See *NFSN's ~10-minute wallclock cull* gotcha.
- `scripts/embed_tick.sh` (new 2026-06-15) — thin stateless cron entry.
  Reads target state from `.embed_state`, `exec`s one bounded
  `embed_opinions --max-runtime 480` pass. No loop, no sentinels.
  Registered as an NFSN scheduled task every ~10 min.
- `scripts/heartbeat.sh` — runs every 10 min via NFSN scheduled task.
  Probes `/healthz`. **No longer supervises/resurrects the embed**
  (rewritten 2026-06-15): it only ALERTS when the `.embed_progress`
  beacon is stale with pending > 0. The resurrect/exit-code/pgrep logic
  is gone.
- `scripts/preflight.sh` (new) — pre-push check that catches
  multi-line `{# #}` template comments and decorator-orphan
  SyntaxErrors before they hit production.
- `opinions/views.py:healthz` (new) — single-`SELECT 1` health probe
  endpoint. No template, no cache, no auth.
- `opinions/paginators.py:NoJoinCountPaginator` — refined to chain
  `.select_related(None).order_by()` before counting. `.values("pk")`
  alone wasn't always enough.
- `run.sh` — `--max-requests 200 → 5000` (25x). The original setting
  was causing user-visible cyclical slowness every few minutes.
- `docketdrift_site/settings.py` — added FileBasedCache backend with
  `_cache_dir = os.environ.get("DOCKETDRIFT_CACHE_DIR")` (persistent
  across worker recycles); added DATABASES OPTIONS
  `init_command: "SET SESSION max_statement_time = 25"`; lowered
  CONN_MAX_AGE 60s → 30s to shrink the post-KILL-QUERY poison window;
  bumped explore_tags cache TTL 15min → 2hr.
- `opinions/context_processors.py:explore_tags_sized` — pre-resolved
  court_ids in the per-tag MATCH-AGAINST COUNTs so the context
  processor doesn't fire 20+ JOIN-COUNTs per templated response.

### Indexable embed flag (2026-06-15 sub-session)

- `opinions/models.py:Opinion.embedding_pending` — new BooleanField,
  default True, db_index'd via composite `(embedding_pending,
  court_id)` named `op_pending_court_idx`. Shadow of the raw VECTOR
  column's IS-NULL state, but indexable.
- `opinions/migrations/0023_opinion_embedding_pending.py` (new) — adds
  the column, the composite index, and a RunPython that backfills
  `embedding_pending = FALSE` from existing `embedding IS NOT NULL`
  state. Starts with `migrations.RunSQL("SET SESSION
  max_statement_time = 0")` so the schema ops don't trip the
  per-statement timeout from settings.
- `opinions/management/commands/embed_opinions.py` — uses
  `embedding_pending = TRUE` in both the count and batch-fetch
  SELECTs; flips the flag in the per-row UPDATE alongside the
  vector. Also raises CommandError instead of `return` on API
  exhaust so the supervisor reads a real non-zero exit code.
  Defaults bumped: `DEFAULT_BATCH 128 → 256`,
  `DEFAULT_MAX_BATCH_TOKENS 60K → 90K`.

### Earlier in the session (2026-06-09 → 2026-06-12)

### New modules / commands

- `opinions/charts.py` (new) — server-rendered SVG line-chart builder.
  Powers the votes-per-year chart on `/judge/<slug>/` and
  `/compare/judges/`. No JS chart library.
- `opinions/paginators.py` (new) — `NoJoinCountPaginator`. Drops
  `select_related` joins AND `ORDER BY` from the COUNT(*) query that
  Django's stock Paginator would otherwise inherit. Used by
  OpinionAdmin, `opinion_list`, `tag_detail`. See the gotcha section
  for why `.values("pk")` alone isn't enough.
- `opinions/parsing/nh.py` (new) — NH Supreme Court parser. Handles
  modern slip-cite format incl. associate-J / chief-C.J. / per-curiam
  bylines, plural-`Case Nos.`, citation-derived case name, "Opinion
  Issued:" date, tail-anchored disposition (incl. `So ordered`).
- `opinions/parsing/statutes_mn.py`, `statutes_nh.py`,
  `statutes_az.py` (new) — per-state extractors. `statutes.py` is now
  a thin state-keyed dispatcher.
- `opinions/management/commands/ingest_pdfs.py` (new) — bulk-ingest a
  directory of opinion PDFs. pypdf text extraction, state-parser-driven
  field population, SHA-256 dedup, optional `pdf_file` storage. Used
  for the 22 NH 2026 opinions and reusable for any future direct
  upload.

### Substantially extended

- `opinions/management/commands/resolve_judges.py` — three big extensions:
  (a) AZ-Supreme plural-prefix handling (`JUSTICES X, Y, and Z`);
  (b) hybrid extraction (use parser, fall back to generic per-field);
  (c) NH dissent footer parsing (`<NAME>, J., dissented.`) with an
  8KB tail window so dissenter lines after the concurrence line still
  match.
- `opinions/management/commands/embed_opinions.py` — `--state <CODE>`
  filter so you can finish one state's corpus before tackling another.
- `opinions/views.py` — added `judge_compare`, `_judge_stats`,
  `_concordance`, `_yearly_panel_votes`, `_attach_match_snippets`;
  extended `opinion_list` with statute-cite redirect, docket-shape
  routing, and snippet generation; `opinion_detail` passes
  `request.GET.q` to the formatter for highlight-on-arrival.
- `opinions/templatetags/opinion_text.py` — paragraph anchors
  (`[¶N]` → `id="para-N"` with self-link), optional highlight
  argument that wraps every match in `<mark>`.
- `opinions/admin.py` — TagSuggestion inline on OpinionAdmin; defer
  raw_text+html_content on the changelist; `paginator = NoJoinCountPaginator`.

### Templates

- `opinions/templates/opinions/judge_compare.html` (new) — side-by-side
  dossier with overlay chart + concordance + split-decisions table.
- `opinions/templates/opinions/_judge_compare_col.html` (new) — partial
  that renders one judge's stat block. Used twice from the parent.
- `opinions/templates/opinions/judge_detail.html` — added voting-
  trajectory chart section + "compare" link on each cohort entry.
- `opinions/templates/opinions/opinion_detail.html` — paragraph
  anchors, `?q=` highlight banner, scroll-to-first-mark script.
- `opinions/templates/opinions/state_home.html` — match-context
  snippet sub-rows under each search result.
- `opinions/templates/opinions/about.html` — first paragraph trimmed,
  hallucination Q&A still present, longer ML-architecture content
  moved to `how_we_differ.html`.
- `opinions/templates/opinions/how_we_differ.html` — receives the
  longer hallucination content.
- `opinions/templates/opinions/state_landing.html`,
  `apex.html` — SEO + schema.org pass: added WebSite + Organization
  + (per-state) Dataset JSON-LD, refreshed canonical URLs, expanded
  meta descriptions.

### Other

- `opinions/static/opinions/css/docketdrift.css` — chart card,
  concordance bar + vote chips, search-snippet rows, paragraph-anchor
  + opinion-find banner, op-para-anchor + `.opinion-body mark`.
- `docs/STATE_ROLLOUT.md` — restructured for future contributors,
  697 lines, Day 1/2/3 timeline + standalone gotcha sections (commit
  `4511c2b`).
- `/home/private/docketdrift/_embed_<state>_loop.sh` (NFSN, not in repo)
  — **removed 2026-06-15** along with `.embed_expected` / `.embed_last_exit`.
  The self-respawning-wrapper pattern is retired; embedding is a bounded
  command driven by an NFSN scheduled task (`scripts/embed_tick.sh`).

### Migrations

- `0023_opinion_embedding_pending` (2026-06-14) — adds the indexed
  shadow flag described above. RunPython backfill ran ~5:47 wall
  clock on the 240K-row table.

`Opinion.reporter_cite` is the next anticipated migration when
roadmap item #10 lands.

## Memory: how Onion likes to work

- Address her as **Onion** (preferred) or **Kellye** (longform/legal).
  **Never Kelly.** Windows OS account `kelly` is just the login.
- She wants me to drive the NFSN deploy loop end-to-end when possible.
  SSH is set up; her preference is "show me the result, don't make me
  run commands."
- She's security-conscious. Default to secure-by-default + explain
  tradeoffs plainly. Don't put secrets in the web root.
- Kids-software philosophy: NEVER propose engagement / monetization /
  attention-extraction patterns. Doesn't apply to DocketDrift directly
  but informs tone — be candid, no growth-hacking dark patterns.
- For multi-hour work, give her clear ASK prompts (AskUserQuestion) with
  the recommended option clearly marked. Then go execute.
- When she says "keep going," she means "close the next gap without
  asking me again until you need a real decision."
