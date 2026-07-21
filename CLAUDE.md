# DocketDrift — notes for Claude sessions

Survival kit for any Claude session working on this repo. Read once,
re-read whenever a recurring gotcha bites. The goal of this document is
to make the next session productive within the first 5 minutes.

## Latest session (2026-07-19) — START HERE

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
   X delivered|authored"), which should help the weak AZ judge/panel
   extraction — **still TODO: re-run `resolve_judges --state AZ` to cash that
   in** (142 panel votes, single-name judges like "Becke").
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

(Numbers as of session-end 2026-06-23.)

Three states live, all on subdomains of `docketdrift.com`:

| State | Subdomain | Opinions | Embedded | Judges | Panel votes | Statute cites | Date range |
|---|---|---|---|---|---|---|---|
| MN (flagship) | `mn.docketdrift.com` | 60,377 | 100% | 124 | 9,914 | 124,858 | 1851 to current |
| NH (beta) | `nh.docketdrift.com` | 20,717 | **100%** | 69 | 17,161 | 79,384 | Through 2026-06-11 |
| AZ (beta) | `az.docketdrift.com` | 38,065 | **100%** | 139 | 142 | 117,045 | Through 2026-06-05 |

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
web-serve `/media/`); GA4 analytics added site-wide. The 2026-06-16
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
- **Analytics is goatcounter ONLY, and collects exactly TWO things.** GA4/
  Google was removed — no visitor data goes to Google. The Author wants to
  know only: (1) **what state** the visitor is from (→ which state to build
  next) and (2) **what device** they're on (→ how much to weight mobile).
  *That's the entire list.* goatcounter derives state/region + device/OS
  server-side; in `base.html` the `path` callback is pinned to a **constant
  `"/"`** and `referrer` to `""`, so we record NO behavior — not which
  opinion/judge/search page was viewed (which pages you read is itself a
  research trail), not the referrer, not the query. Do NOT re-add page-path,
  referrer, event, or custom-dimension tracking without the Author's say-so.
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
| `resolve_judges --state <CODE> [--create-missing] [--since YYYY-MM-DD]` | Match byline+panel to existing Judge rows; `--create-missing` mints new ones. Hybrid: state parser fills what it knows, generic byline extractor fills the rest. | per state | yes |
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
