# Monitoring — what watches DocketDrift, and what still doesn't

Written 2026-09-26, the day the latency monitor shipped. The question that
prompted it: *how do we build the system that tells us about a problem,
instead of finding it by accident and fixing it retroactively?* Seven
consecutive sessions each found a live defect; one was found by a monitor.

## The principle

Every monitor here exists because something broke silently first. That is
the wrong order. The right order is to write down the **invariants** — the
things that must be true for the site to be doing its job — and give each
one a check that runs on a cadence and alerts on a **transition**. A monitor
that alerts on a *state* every tick trains the reader to delete the email,
which is exactly how `cited-by` 500'd for twelve days.

Three rules every check here follows:

1. **A blind monitor fails loud.** Unreadable log, zero parsed lines, a
   missing field: exit non-zero with "I am blind", never "ok". A check that
   silently reports green when it cannot see is worse than no check.
2. **Alert on a page TYPE, a shape, a trend — never on a raw count.** Raw
   counts drift with corpus size and crawler weather; the 5xx monitor cried
   wolf until it was rewritten to fire on concentration.
3. **Ride an already-registered task.** Every NFSN scheduled task needs a
   member-panel step, and two monitors sat broken for months behind one
   (a missing `/ho` in a path, with "Last Run" populated the whole time).
   New checks bolt onto `heartbeat.sh` (every 5–10 min) or
   `freshness_check.sh` (weekly).

## What is watched today

| Invariant | Check | Cadence | Alerts on |
|---|---|---|---|
| The site answers at all | `heartbeat.sh` → `/healthz` (one `SELECT 1`) | 5–10 min | non-200 |
| The embed pipeline advances (overnight window only) | `heartbeat.sh` → `.embed_progress` beacon age | 5–10 min | stale beacon with pending > 0 |
| **Pages are not slow** (starvation) | `latency_check.sh`, from `heartbeat.sh` | 5–10 min | per-shape p50 over threshold, or overall p50; **transition only** |
| Each state keeps receiving opinions | `check_freshness` via `freshness_check.sh` | weekly | newest opinion older than 45d (MN/AZ) / 60d (NH) |
| The residential scrapers actually ran | `freshness_check.sh` → `.scrape_{mn,nh}_last` beacons | weekly | beacon missing or > 9 days |
| No page TYPE is broken | `error_rate_check.sh`, from `freshness_check.sh` | weekly | a shape ≥10% 5xx with ≥10 errors; or site-wide ≥1% |
| Long backfills don't die silently | NFSN emails on any non-zero task exit | per run | non-zero exit |

`latency_check.sh` also appends one line per run to
`/home/logs/latency_check.log`. That file is the baseline: re-tune the
thresholds in the script's awk `BEGIN` block from it, not from reasoning.

## What is NOT watched — the known blind spots, in the order they'd hurt

1. **Nothing looks from OUTSIDE.** Every check runs on the NFSN box and hits
   the internal gunicorn address. DNS, the TLS certificate, the NFSN front
   proxy, and the subdomain aliases are all unmonitored. A cert expiry would
   take the site down while every check above reports green. Fix: one
   external uptime probe of `https://mn.docketdrift.com/healthz` from a free
   third-party pinger, alerting to `hello@`. This is the cheapest item on
   this page and the largest hole.
2. **The 5xx check is weekly.** A page type can break on Tuesday and be found
   the following Monday. It is cheap enough (tail + awk) to move onto
   heartbeat with the same transition logic latency uses. Not done yet
   because its shape threshold is calibrated to a week's volume.
3. **Data invariants have no check.** The defects that cost the most —
   MN's #1 statute being a footer, every lettered chapter misfiled, 10,420
   truncated captions, the phantom judges — were all *wrong answers* found
   by a number that could not be right. A daily `check_invariants` command
   should assert the cheap ones: no future release dates; per-state
   opinion count never decreases; a seated judge has voted within N months
   (`audit_judges` already has most of this); the most-cited statute per
   state is not >3× the second; zero opinions with an empty title created
   in the last week; every live state's sitemap index returns 200 and its
   first chunk parses.
4. **The scheduled tasks themselves.** "Last Run" populates even when a task
   fails instantly. A task that stops being *registered* is invisible. The
   beacon pattern (a file the task stamps only after verified work, checked
   by a *different* task) covers embed and the two scrapers; it does not
   cover `precomptags`, `aicitations`, or the five `ingest*` jobs.
5. **The access log going sparse or huge.** `rotate_access_log.sh` is run by
   hand. At ~75–90K requests/day the log passes 1 GB every few months; an
   Applebot-scale surge does it in weeks.
6. **Per-state shapes.** The access log records the path, not the Host, so
   one state's broken page dilutes to ~1/N of its shape. At four states a
   fully broken page reads as ~25%; at eight it slips under the 10% bar.
   Putting the Host in the log is privacy-neutral and fixes both the 5xx
   and the latency check at once. Revisit at state #5.

## How to add a check

- Decide the invariant in one sentence. If it can't be stated, it can't be
  checked.
- Bound the scan (tail, LIMIT, `--max-runtime`) so NFSN's ~40s CPU cull
  cannot turn it into a false ALL CLEAR.
- Make "cannot see" a failure.
- Alert on a transition; keep state in a dotfile under `/home/private/docketdrift/`.
- Print the actionable half (which shape, which state, how much) — the
  email is the whole interface.
- Bolt it onto `heartbeat.sh` (minutes) or `freshness_check.sh` (weekly).
  Do not register a new task unless the cadence genuinely needs it.
- Test it against a fixture that is broken, not only one that is healthy.
