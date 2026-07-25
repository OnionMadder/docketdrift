# DocketDrift — working backlog

Snapshot 2026-07-24. Prioritized. Each item says what it is, why it matters,
and roughly how big. "Onion" items need the member panel or are editorial.

Status baseline (pulled live 2026-07-24): 119,250 opinions, all embedded.
Dispositions MN 97.6% / NH 78.5% / AZ 67.7%. Holdings 39,402. Panel votes
56,164. Citation graph 605,423 edges.

---

## Tier 0 — clean up the working tree (it's a landmine)

The repo has carried the same uncommitted pile all session. Two parked
features are entangled across shared files, and one parked change is unsafe
to ship. Decide each, then the tree is clean.

- [ ] **Do NOT ship `about.html` / `how_we_differ.html` as-is.** The parked
  rewrites assert an LLM-generated holdings surface in indexed schema.org
  JSON-LD — false now that holdings are *extractive*. Either rewrite them for
  the extractive method or revert. **Highest-risk item on the board.**
- [ ] **Decide citation clustering (migration 0027).** "How this document has
  been cited" (Scholar-style). Migration is committed; the pipeline
  (`cluster_citations`, `embed_citations`), `opinion_cited_by.html`, and the
  `views.py`/`citations*.py`/`_treatment_panel.html` edits are uncommitted and
  unrun. Ship it NH-first (cheap Voyage pass) or formally park it. Medium.
- [ ] **Delete stale files.** `session-brief.md` (superseded by CLAUDE.md +
  ROADMAP). The old LLM `extract_holdings.py` command + `holding_review*`
  admin templates — kept only if we ever run an LLM residual pass; otherwise
  cut. Small.
- [ ] **Retarget the holdings panel link** — it points at a generic "how we
  work" page. Repoint once `/how-we-differ/` documents the extractive method.

## Tier 1 — editorial polish (the honest asterisks on the dashboard)

- [ ] **Review the 280 forged AZ judges.** `resolve_judges --create-missing`
  minted 280 UNKNOWN-status AZ judges from bylines (surname-only like "Becke",
  full-name dupes, some non-judge noise). Votes are structurally correct, but
  the roster needs a review/merge/cull pass in admin before AZ judge pages are
  clean — same human step MN/NH had. Onion (admin work).
- [ ] **Holdings page-number artifacts (~5%).** ~897 MN + ~460 AZ holdings
  have a stray PDF page number mid-sentence ("determin**ing 2 t**hat"). It
  corrupts the verbatim-quote promise. Deterministic fix in `holdings.py`
  (strip lone digits sitting between lowercase words at page breaks) +
  re-extract. Small–medium; needs care not to strip real numbers.

## Tier 2 — automation (turn finished work autonomous)

All member-panel / logged-on — Onion registers, not scriptable by me.

- [ ] **Register `run_mn_weekly.ps1`** as a logged-on Windows Task Scheduler
  entry (mirror the NH task). Makes MN COA forward-fill autonomous.
- [ ] **Register NFSN `ai-citations`** scheduled task (weekly AI-usage digest
  email): `/bin/sh /home/private/docketdrift/scripts/ai_citations.sh 7`.
- [ ] **Register NFSN `freshness-check`** scheduled task (weekly staleness
  monitor): `/home/private/docketdrift/scripts/freshness_check.sh`.

## Tier 3 — coverage (bigger builds)

- [ ] **MN COA deep backfill** for the thin years (2017–2023). Attended sweep
  with the new scraper — walk the pager in bounded windows, solving the
  occasional CAPTCHA. Forward-fill is already done. Medium, attended.
- [ ] **Finish the CL `/clusters/` catch-up ingest.** Only 12 MN COA clusters
  were pulled as a smoke test after the `/search/`→`/clusters/` fix. Work back
  through MN/AZ/NH in bounded runs (`--since` + `--limit`), watching for 429s.
- [ ] **Repair the 14,440 synthetic `CL-<id>` case numbers.** They're
  unreachable by the only identifier a lawyer has (the real docket). Needs a
  deliberate in-place rewrite pass (`update_or_create` would duplicate). Medium.

## Tier 4 — throughput & hardening (lower priority)

- [ ] **Triage the ~50K tag-review queue** via the pile-picker admin. Onion.
- [ ] **Harden the tag-review heavy slice** — self-bind slice/count queries
  with `SET STATEMENT max_statement_time` + catch-and-close (poison-cascade
  defense), mirroring `semantic.py`.
- [ ] **Fix the broken per-page Twitter-card meta** — `{{ self.og_title }}`
  is a Jinja idiom that no-ops in Django, so every X card shows the generic
  title (from `docs/POLISH_OPPORTUNITIES.md`). Small.
- [ ] **Beta/Flagship labels undersell the 60K MN corpus** (stamped "Beta"
  while smaller NH is "Flagship"). Copy-only. Small.
- [ ] **State-router middleware lookup cache** — `_resolve_state` hits the DB
  every request; cache by Host header per worker lifetime.

## Diminishing returns (only if a frequency scan shows a new cluster)

- NH's remaining ~4,465 no-match dispositions (genuine 19th-c. prose).
- AZ's ~12K no-match dispositions (genuinely historic AZ text).
- These are the pre-modern tail; not worth a pass unless a scan finds a
  matchable cluster.
