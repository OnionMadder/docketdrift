# MN Court of Appeals coverage backfill — order / nonprecedential opinions

Status: **BUILT + VALIDATED 2026-07-20.** Scraper lives at
`scripts/mn_scraper/scrape_mn_coa.py` (+ `run_mn_weekly.ps1`), mirroring the
NH model. 19 real COA opinions scraped → ingested → live end-to-end on first
use (2026-07-20 and 2026-07-13 filings). The rest of this doc is the original
2026-06-27 recon; **see "BUILD NOTES (2026-07-20)" at the bottom for what was
actually true when built — several recon details were stale.**

Written 2026-06-27 after the Rickmyer v. Brooks (A25-0969) miss surfaced a
systematic gap. See the memory note `project_mn_coa_courtlistener_gap`.

> **Caveat on the premise:** the CL under-ingestion this doc blames was
> *partly* our own bug — `ingest_court` listed via `/search/` (published-only)
> instead of `/clusters/`, fixed 2026-07-20. So CL carries more MN COA than
> this doc assumes. The scraper was still built deliberately: an owned,
> debuggable pipeline independent of CL, matching NH (the one source that
> "just works"). Both pipelines now feed MN and dedup against each other.

## The problem

DocketDrift ingests MN (and AZ) from **CourtListener** (`ingest_court` →
CL `/search/?type=o&court=minnctapp`). CL's coverage of recent MN Court of
Appeals dispositions — **especially nonprecedential and order opinions** —
is thin and lagging. Opinions that exist publicly are absent from our corpus
because CL never had them.

Confirmed example: **Rickmyer v. Brooks, A25-0969** (COA order opinion, HRO
appeal, filed 2026-01-06). Not in CL's search at all; not in our corpus until
hand-ingested from Onion's PDF on 2026-06-27. `ingest_court` does **not**
filter by type — it ingests every cluster CL returns — so the gap is purely
source-side.

Measured scope (2026-06-27), our corpus vs CL's own live search, MN COA:

| Year | In our corpus | (nonprecedential) | CL live search |
|---|---|---|---|
| 2024 | 463 | 175 | 288 |
| 2025 | 148 | 92 | 56 |
| 2026 | 119 | 102 | 23 |

Both columns are far below MN COA's true output (a busy intermediate court
issuing published + unpublished + order opinions **every week**). The hole is
worst in recent years and in the nonprecedential/order slice. Order opinions
(the Rickmyer class) are the most reliably missing.

**Exact missing-count is not yet known** — see "Why we can't just count it."

## Why we can't just count it (the enumeration blocker)

The authoritative index is the **Minnesota State Law Library** appellate
opinions archive (`mn.gov/law-library/...`, quarterly static pages
`capYYqN` = published, `cauYYqN` = unpublished + order). That host sits
behind **ShieldSquare / PerfDrive bot protection**: a server-side fetch
(WebFetch, curl, requests) is 302-redirected to `validate.perfdrive.com`.
Same class of wall as the NH/AZ Akamai blocks — a residential real-browser
session is required to read it.

`mncourts.gov/courtofappeals/recentopinions` (and `/order-opinions`) **are**
fetchable server-side, but only show the **current week** (e.g. 3 order
opinions for the week of 2026-06-22), with no machine-readable archive — they
just link out to the bot-protected law-library archive for history.

So: producing a hard "we're missing N of M" number is itself gated behind the
same residential-browser requirement as the fix. The enumeration falls out of
the pipeline build (step 1 below), not before it.

## What's already done

- **Parser handles the order-opinion format** (commit `ab27442`,
  `opinions/parsing/mn.py`). Order opinions use `Dated: <date>` instead of
  `Filed <date>`, split the caption across blank lines, and carry a distinct
  "this order opinion is nonprecedential" footer. The parser now reads all
  three (additive — regular opinions parse identically). Verified on A25-0969:
  case_number + 2026-01-06 + nonprecedential + full panel.
- **`ingest_pdfs --state MN --court appeals`** is the proven ingest path
  (same command NH uses). One-off ingest of A25-0969 succeeded end-to-end
  (row + attached PDF + `embedding_pending=True` → overnight embed).

### Known parser quality gaps (order opinions only; non-blocking)

- **Disposition**: falls back to a whole-document scan (confidence 0.45) and
  grabs the leftmost disposition word, which for an order opinion is often the
  *district court's* action (Rickmyer → "Denied", though the COA affirmed).
  An order-opinion-specific disposition extractor (the operative
  "IT IS HEREBY ORDERED … affirmed/reversed" line) would fix this.
- **Title artifact**: PDF text layers sometimes space out words
  ("R e s pondent"). Cosmetic; could be de-spaced in the parser or cleaned in
  admin.

## The pipeline (mirror the NH model)

NH solved the identical problem (Akamai-blocked source, CL lag) with a
**residential Playwright scraper run off-platform** → PDFs → `ingest_pdfs`.
MN COA should follow the same shape. See `scripts/nh_scraper/` and the memory
note `project_nh_two_ingest_pipelines`.

1. **Residential scraper** (`scripts/mn_scraper/scrape_mn_coa.py`, to build) —
   real Chrome via Playwright (`channel="chrome"`, headed) on Onion's Windows
   box. **Feasibility CONFIRMED 2026-06-27:** headed real Chrome loads mn.gov
   cleanly — ShieldSquare does NOT challenge a real browser (it only bounces
   non-browser clients like WebFetch/curl to `validate.perfdrive.com`), exactly
   like the NH/Akamai pattern. The bot wall is not a blocker for this approach.
   Source options to evaluate:
   - **`opinions-archive.jsp` date-range search** (`mn.gov/law-library/search/
     opinions-archive.jsp`): the real, current interface — paginated results
     filterable by court (precedential / nonprecedential / Supreme) and date
     range. This is the robust path for both **backfill** and the enumeration
     denominator (search a date range, count, diff against our corpus). NOTE:
     the older static quarterly pages (`cauYYqN` / `capYYqN`) are stale — the
     guessed `cau25q1.html` 404s, so recent years aren't at that URL pattern.
     Drive the search UI, not static archive URLs.
   - **mncourts.gov recent + P-MACS**: best for the **weekly forward-fill**
     (current week; the recentopinions pages are fetchable even server-side).
   Pull PDFs with an in-page same-origin `fetch` (the NH lesson: the request
   API is fingerprinted; only the real browser context gets through).
2. **Ship + ingest**: `scp` PDFs to NFSN, then
   `ingest_pdfs --dir <tmp> --state MN --court appeals` (dedups on
   `(court, case_number)`, so re-grabbing boundary opinions is harmless).
3. **Embed**: new rows land `embedding_pending=True`; the overnight embed tick
   picks them up. Optionally `extract_statutes --state MN` to fold them into
   the statute graph.
4. **Cadence**: weekly forward-fill (a scheduled run after Monday 10:00 CT),
   plus a one-time backfill sweep over the quarterly archives for the years
   we're thin (2024-2026 first).

## Recon findings (2026-06-27) — the technical map for the build

Confirmed by probing from NFSN (server-side) and headed Chrome (residential):

- **PDFs download directly from NFSN** — no bot wall on the
  `mn.gov/law-library-stat/archive/` host (verified: `HTTP 200
  application/pdf`, valid PDF). So PDF *fetching* can be server-side; only the
  *listing* needs the browser.
- **PDF URLs are deterministic by category + case number:**
  `//mn.gov/law-library-stat/archive/<cat>/a<NNNNNN>.pdf`, where
  `a260529` = case **A26-0529** and `<cat>` is:
  - `COAspectorders` = COA **order opinions** (the Rickmyer class)
  - `ctapun` = COA **unpublished** (nonprecedential)
  - `ctappub` = COA **published** (precedential)
  (Supreme Court opinions live under other dirs — filter to these three for COA.)
  So **case number + type come from the URL alone**; date / case name /
  disposition come from the PDF via the MN parser (which now handles orders).
- **The search/listing IS bot-walled except via a real browser.** The dynamic
  search endpoint (`xml.jsp` → Vivisimo `search.wcm.mnit.mn.gov`) returns a
  **Radware Bot Manager Captcha** to NFSN curl/WebFetch. **Headed real Chrome
  passes it cleanly** (no captcha, real results render). So the listing step
  needs residential Playwright — MN lands at the **same model as NH**, not a
  pure NFSN cron.
- **Search mechanics:** `opinions-archive.jsp`, a `query` text field taking
  Vivisimo syntax (`date:>YYYY-MM-DD`), Vivisimo-backed, pagination via
  `root-<offset>-10` (10/page). **Caveat:** the search form + results are
  JS-injected and load-timing-variable (present on one load, absent the next),
  so the scraper must use robust `wait_for_selector`/networkidle retries, NOT
  fixed `wait_for_timeout` sleeps. This is the main thing that makes the build
  iterative.

**Resulting build shape (residential Playwright, headed):**
1. Open `opinions-archive.jsp`, submit `date:>{since}` (wait robustly for the
   results form/list to render).
2. Paginate, collecting every href matching
   `archive/(COAspectorders|ctapun|ctappub)/a\d+\.pdf` in range.
3. Download those PDFs (in-page same-origin `fetch`, the NH lesson) OR hand the
   URL list to NFSN to curl directly (PDFs aren't walled there).
4. `scp` → `ingest_pdfs --dir <tmp> --state MN --court appeals` (dedups) →
   overnight embed → `check_freshness` verifies.
5. Wrap (`run_mn_weekly.ps1`, mirror `run_nh_weekly.ps1`) + register a weekly
   Windows task; plus a one-time backfill sweep over the years we're thin.

Status: recon + feasibility DONE; scraper not yet written (the JS-flaky search
UI makes it an interactive build best done in a focused session).

## Open decisions for Onion

- **Backfill depth**: how far back to sweep the law-library archives? (Recent
  3 years closes the worst of it; full archive is bigger but bounded.)
- **Scope**: order opinions only, or all COA nonprecedential CL is missing?
  (The scraper can grab the whole `cau` list; cost is just more PDFs.)
- **Supreme Court too?** This doc is COA-scoped (where the gap was found); MN
  Supreme coverage should be spot-checked the same way.
- **Automation**: where does the weekly scraper run — manual, Windows Task
  Scheduler, or a residential always-on box? (NH is currently manual; see
  `project_nh_two_ingest_pipelines`.)
- **Disposition fix**: build the order-opinion disposition extractor before or
  after the backfill? (Before = cleaner data on first ingest; after = don't
  block the backfill on parser polish, re-run `ingest_pdfs --update` later.)

## BUILD NOTES (2026-07-20) — what was actually true

Built live against the site. Several 2026-06-27 recon details had gone stale;
these are the corrected facts the scraper is written against.

- **PDF URL scheme (recon doc was WRONG).** Not `archive/<cat>/a<NNNNNN>.pdf`.
  The live paths are **`archive/<cat>/<year>/OP<case>-<mmddyy>.pdf`**, e.g.
  `archive/ctappub/2026/OPa251985-071326.pdf`. Categories confirmed:
  `ctappub` (COA published), `ctapun` (COA unpublished), `COAspectorders`
  (COA orders), `supct` (Supreme — excluded). Case letter case varies
  (`OPa…`/`OPA…`), so the category regex is case-insensitive.
- **No search submission needed.** `opinions-archive.jsp` lands on a
  reverse-chronological list of ALL recent opinions (all three COA categories
  + Supreme pre-checked), 10/page, ~1,400 deep, each row carrying a case
  title, `Date: <Month D, YYYY>`, and the PDF link. The scraper pages this
  newest-first and keeps COA PDFs with row-date ≥ `--since`. Metadata is
  re-derived from each PDF by the MN parser at ingest, so the scraper only
  needs the URL + row date (to bound the walk).
- **NEVER `networkidle`, never fixed sleeps.** A live chat widget keeps the
  network busy forever, so `networkidle` never fires (this is the "JS-flaky"
  symptom the recon flagged). Wait for the result anchors with a
  reload-on-empty retry instead.
- **The bot wall is real but manageable.** Radware serves a CAPTCHA to rapid
  reloads / deep pagination, but a **single fresh page-1 load passes cleanly**.
  So: (1) a PERSISTENT browser profile (`launch_persistent_context`) banks any
  cleared challenge across runs; (2) access is paced; (3) a CAPTCHA is **never
  auto-solved** — the scraper waits for the logged-on human to clear it in the
  visible window, else proceeds with page 1. This is the same "run only when
  logged on" constraint as NH.
- **Pagination = real `?url=…root-<offset>-10` URLs** with a per-session
  vivisimo token; follow the page's own hrefs through the robust loader, never
  construct them. Page 1 (10 newest) is reliably captcha-free → that's where
  weekly forward-fill lives (`run_mn_weekly.ps1`, `--max-pages 3`). Deep
  backfill over many pages is a **separate attended sweep** (the human solves
  the occasional CAPTCHA); the scraper stops cleanly at the visible pager
  window (pages 1–10) and says so rather than silently truncating.

### Still open

- **Deep backfill** (years we're thin) not yet run — it's the attended,
  many-page sweep. The weekly forward-fill IS built and reliable.
- **Register the Windows Task Scheduler entry** for `run_mn_weekly.ps1`
  (mirror the NH task; run only when logged on).
- Order-opinion disposition polish (whole-doc-scan grabbing the district
  court's action) still stands — non-blocking; re-run `ingest_pdfs --update`
  after a parser fix.
