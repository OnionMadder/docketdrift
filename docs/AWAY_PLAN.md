# While Onion is away (from 2026-10-17, ~1 week)

Work Claude can do without a decision from Onion, plus the short list that
waits for her. Each task is one Dispatch prompt. Copy the **Prompt** line.

## Ground rules for every unattended session

1. **Tests and `manage.py check` pass locally before any push.** Run `check` as
   its own command, never piped into a restart.
2. **Verify on prod in-process before restarting gunicorn**, then over public
   HTTPS after. Read the whole `git pull` output and confirm the commit landed.
3. **For about a minute after a restart, NFSN's proxy serves stale 502/503s.**
   Judge health by the gunicorn log (a fresh "Booting worker" line, then 200s)
   and re-curl after a minute. Seen 2026-10-16: every host 503'd for about 30
   seconds while the new worker was already serving 200s.
4. **Never run `precompute_explore_tags` after a restart** if browser-UA opinion
   traffic is heavy (the 2026-10-05 lesson). Let the cache fill on demand.
5. **No outward actions:** no email, no court contact, no Search Console or
   Bing, no posting. Draft them into the hand-back note instead.
6. **No editorial calls and no vote moves.** Anything that reassigns votes,
   merges judges, deletes opinions, or decides what counts as boilerplate gets
   measured, written up, and left for Onion.
7. **Chunked sweeps:** advance only on a printed `resume with:  --min-id N`
   trailer, and retry the same cursor on a missing one. Write before-numbers
   down first.
8. **End every session** by updating `docs/TODO.md` and adding one line to the
   log at the bottom of this file: what ran, what changed, anything that needs
   Onion.

## A. Safe to run unattended, in this order

**1. ~~Louisiana court rules~~ — built 2026-10-16; the sweep was running at hand-off. If `la.r.` rows are missing, re-run the chunk driver from the cursor in TODO.md.** Original note: Both editorial calls are made: flag the Rule
2-16.3 not-for-publication notice and the Rule IV, Part 2 panel footnote.
Mirror `rules_az.py` / `rules_nh.py`, measure misses on real output, sweep
with the chunk driver, run the integrity check (re-extract 600 random
opinions: 0 mismatches, 0 duplicate rows).
- **Prompt:** "Build the Louisiana rule extractor per docs/AWAY_PLAN.md task 1 and docs/TODO.md, then deploy and verify."

**2. ~~Arizona bare statute numbers~~ — DONE and live 2026-10-16.** Original note: `13-1105` text-searches (8s) instead of
routing to the statute page. Add `bare_slug_candidates` to `statutes_az.py`,
using MN's as the template. It must not swallow rule numbers. Tests first.
- **Prompt:** "Do AWAY_PLAN task 2: AZ bare statute routing."

**2b. Arizona statute COVERAGE gap (new 2026-10-16).** `statutes_az.py` only
accepts `A.R.S. §`. Real text also writes `A.R.S. section 13-1410` (399 cites
in 73 of 1,214 sampled opinions), `(“A.R.S.”) section ...`, `A.R.S. §§ ...`
lists and `A.R.S 13-...` without the final period. Measure, extend the grammar
with tests, then re-sweep `extract_statutes --state AZ --force` with the chunk
driver. Write the before-numbers down first: a `--force` chunk that gets
killed leaves its window short, so retry the same cursor.
- **Prompt:** "Do AWAY_PLAN task 2b: AZ statute 'section' forms."

**3. Daily data-invariant check** (`docs/MONITORING.md` blind spot #3). Cover:
future release dates, opinions with no court, rule or statute slugs whose
display number disagrees with the slug, a seated judge with no vote in 120
days. Alert on *transition* the way `latency_check.sh` does. Chain it into
`heartbeat.sh`, not a new scheduled task (a new task needs the member panel).
- **Prompt:** "Do AWAY_PLAN task 3: build check_invariants and chain it into heartbeat."

**4. 5xx check onto heartbeat** with transition alerting (TODO line ~1403).
- **Prompt:** "Do AWAY_PLAN task 4."

**5. Roster probe, all four states, report only.** The checks: seated but
silent, voting but unseated, court mismatch. Write the findings to TODO.md
and **move nothing**.
- **Prompt:** "Do AWAY_PLAN task 5: run the roster probe on MN/NH/AZ/LA and report."

**6. Finish the AZ/LA 2026 COA catch-up from CourtListener** (TODO, top
block). Use `ingest_court --skip-existing --until` in `timeout 540` chunks and
honor long Retry-After penalties. Then run the derived passes on the new
top-of-PK ids: statutes, citations, holdings text, rules, judges, spans.
- **Prompt:** "Do AWAY_PLAN task 6: resume the AZ/LA COA catch-up."

**7. Arizona Division Two after its website move (Oct 16-19).** Repoint the
nine "Official bio" links only after opening each new page and confirming it
names the right judge. Check whether the new site carries judge photos. If it
does, report; **don't** fetch them without Onion (portrait rule: court-supplied
or monogram).
- **Prompt:** "Do AWAY_PLAN task 7 (run on or after Oct 20)."

**8. Freeze investigation, read-only.** Run `stallprobe.py` in a loop for
about 20 minutes so it overlaps a burst, and capture SHOW PROCESSLIST when
`/healthz` goes over 1s (2026-10-05 block). Report only.
- **Prompt:** "Do AWAY_PLAN task 8."

**9. Daily status check** (good as a recurring Dispatch). Gather:
- the four state landings
- p50/p95 from `latency_check.log`
- 5xx in the last 24h
- newest opinion per state and the scraper beacons
- whether a scraper is on the site (browser-UA opinion rate)

Reply with five lines and act on nothing.
- **Prompt:** "Daily DocketDrift status check per AWAY_PLAN task 9."

## B. Waiting for Onion

- **Deploy authority while away:** may unattended sessions push and restart
  after tests and in-process checks pass? (Asked 2026-10-16.)
- AZ Division Two portraits: email the court, as with Division One's HR office.
- MN/AZ 1989–2014 coverage trough: disclosure wording.
- Composite `(court_id, disposition_bucket)` index: a 2.75GB migration that
  needs a quiet window and an explicit yes.
- Search Console / Bing resubmissions; the external uptime probe (third-party
  signup).
- **Louisiana clerk's delivery certificate** ("HAS BEEN DELIVERED IN
  ACCORDANCE WITH UNIFORM RULES - COURT OF APPEAL, RULE 2-16.4 ... THIS DAY")
  is flagged as a notice. Same class as 2-16.3, but it was not one of your two
  calls: confirm or reverse.
- **NH's most-cited rule is Sup. Ct. R. 18(1)** at 998 opinions: "oral
  argument is unnecessary in this case". It's counted under the 20(2) call.
  Confirm you want that recital leading the NH leaderboard.
- Any judge vote move or merge that the roster probe (task 5) turns up.
- Deleting `_dd2.py` and `_regress_caption.py` from prod (harmless; just ask).
- FLP thread #1115 follow-up.

## Log

- 2026-10-16: rule surfaces finished; AZ rules live (49,848); NH extractor
  built; found and fixed MN Evid. 1101→110 / 1002→100 truncation (242 wrong
  rows on two public pages) and range-to-first-member in AZ/NH; NH sweep plus
  AZ/MN re-sweeps run this session.
