# DocketDrift — working backlog

Snapshot 2026-07-24. Prioritized. Each item says what it is, why it matters,
and roughly how big. "Onion" items need the member panel or are editorial.

Status baseline (pulled live 2026-07-24): 119,250 opinions, all embedded.
Dispositions MN 97.6% / NH 78.5% / AZ 67.7%. Holdings 39,402. Panel votes
56,164. Citation graph 605,423 edges.

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

- [ ] **Ship or drop citation clustering.** It's parked on the branch above.
  To ship: confirm 0027 columns on prod (they are), run
  `extract_citations`→`embed_citations`→`cluster_citations` (small Voyage
  cost), wire + validate `opinion_cited_by` NH-first. Medium.
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
- [x] **Fix the broken per-page Twitter-card meta.** ✅ DONE 2026-07-25.
  `twitter:title`/`description` used the no-op `{{ self.og_title }}` Jinja
  idiom, so every X card showed "DocketDrift". Fixed by dropping the broken
  twitter:* tags and relying on the (working) og:* fallback — one source of
  truth. Also fixed a latent bug: judge cards now use the judge photo
  (og:image fallback) instead of the generic cover. Verified live on opinion,
  judge, and apex pages.
- [ ] **Beta/Flagship labels undersell the 60K MN corpus** (stamped "Beta"
  while smaller NH is "Flagship"). Copy-only. Small.
- [ ] **State-router middleware lookup cache** — `_resolve_state` hits the DB
  every request; cache by Host header per worker lifetime.

## Diminishing returns (only if a frequency scan shows a new cluster)

- NH's remaining ~4,465 no-match dispositions (genuine 19th-c. prose).
- AZ's ~12K no-match dispositions (genuinely historic AZ text).
- These are the pre-modern tail; not worth a pass unless a scan finds a
  matchable cluster.
