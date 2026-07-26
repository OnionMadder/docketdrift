# DocketDrift — working backlog

Snapshot 2026-07-24. Prioritized. Each item says what it is, why it matters,
and roughly how big. "Onion" items need the member panel or are editorial.

Status baseline (pulled live 2026-07-24): 119,250 opinions, all embedded.
Dispositions MN 97.6% / NH 78.5% / AZ 67.7%. Holdings 39,402. Panel votes
56,164. Citation graph 605,423 edges.

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

**Now monitoring (nothing to build — this is a wait):**

- [ ] **~days:** grep the access log for Googlebot hitting mn/az opinion pages
  (first crawl = submission took). Also watch Search Console Pages report move
  URLs "Discovered" → "Indexed".
- [ ] **~weeks:** re-run `ai_citation_profile`. MN/AZ appearing = the whole
  thread (reporter cites + sitemap fix + Search Console) paid off. Baseline to
  beat: NH 96% / AZ 4% / MN 0%.
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

- [ ] **Review the 280 forged AZ judges.** `resolve_judges --create-missing`
  minted 280 UNKNOWN-status AZ judges from bylines (surname-only like "Becke",
  full-name dupes, some non-judge noise). Votes are structurally correct, but
  the roster needs a review/merge/cull pass in admin before AZ judge pages are
  clean — same human step MN/NH had. Onion (admin work).
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

- [ ] **`ai-citations`** — weekly "who's citing us" digest (emails stdout).
  - Tag: `ai-citations`
  - Command: `/bin/sh /home/private/docketdrift/scripts/ai_citations.sh 7`
  - Schedule: weekly (e.g. Monday ~08:00)
  - Shows which opinions live AI agents (chatgpt-user / claude-user /
    perplexity-user) fetched to answer a question, vs. training crawlers.

- [ ] **`freshness-check`** — weekly staleness alarm (emails only on failure).
  - Tag: `freshness-check`
  - Command: `/bin/sh /home/private/docketdrift/scripts/freshness_check.sh`
  - Schedule: weekly, **after** the Monday ingests land (e.g. Tuesday 12:00 UTC)
  - Exits non-zero + emails a loud alert only when a state's newest opinion
    crosses its threshold (MN/AZ 45d, NH 60d); silent on a healthy week.

Already registered + running (nothing to do): `embed-tick` (~10 min),
`heartbeat` (~10 min), `cron-ingest` (weekly CL ingest).

### Windows Task Scheduler (residential box, NOT NFSN)

- [ ] **`run_mn_weekly.ps1`** — logged-on Windows Task Scheduler entry mirroring
  the NH task (`scripts\mn_scraper\run_mn_weekly.ps1`, run-only-when-logged-on).
  Makes MN COA forward-fill autonomous.

## Tier 3 — coverage (bigger builds)

- [ ] **MN COA deep backfill** for the thin years (2017–2023). Attended sweep
  with the new scraper — walk the pager in bounded windows, solving the
  occasional CAPTCHA. Forward-fill is already done. Medium, attended.
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
- [ ] **Repair the 14,440 synthetic `CL-<id>` case numbers.** They're
  unreachable by the only identifier a lawyer has (the real docket). Needs a
  deliberate in-place rewrite pass (`update_or_create` would duplicate). Medium.

## Tier 4 — throughput & hardening (lower priority)

- [ ] **Triage the ~50K tag-review queue** via the pile-picker admin. Onion.
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
