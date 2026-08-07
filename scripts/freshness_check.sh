#!/bin/sh
# Weekly per-state ingest freshness monitor.
#
# Register as an NFSN scheduled task (member panel -> Manage Scheduled Tasks),
# e.g. weekly, a few hours after the latest ingest cron of the week:
#   Tag:      freshness-check
#   Command:  /home/private/docketdrift/scripts/freshness_check.sh
#   Schedule: weekly (e.g. Tuesday -- after Mon MN COA + the CL crons land)
#
# The wrapped command exits non-zero with a loud, actionable stderr block when
# any live state's newest opinion is older than its per-state staleness
# threshold; NFSN emails that block to the site owner. On success it prints a
# freshness table to stdout (also captured in the task log) and exits 0.
#
# This is the safety net for the per-state scraper model: a scraper/cron that
# silently stops looks identical to a quiet docket. See the check_freshness
# management command for the thresholds and rationale.
cd /home/private/docketdrift
fail=0
.venv/bin/python manage.py check_freshness || fail=1

# --- Windows-side scraper beacons -------------------------------------------
# Each residential weekly scraper stamps an epoch timestamp here on SUCCESS
# (including the legitimate "court published nothing new" path). A scraper
# that crashes -- e.g. the 2026-08-03 run, which failed with Last Result 1
# when it collided with an attended backfill session holding the same Chrome
# profile -- goes silent, and NOTHING alerts on a Windows task's exit code.
# Without this check that silence only surfaced at the 45-day corpus
# threshold; with it, one missed weekly run alerts at the next freshness run.
check_beacon() {
  # $1 = label, $2 = beacon file, $3 = max age in days
  now=$(date +%s)
  if [ ! -f "$2" ]; then
    echo "FRESHNESS: $1 scraper beacon MISSING ($2) -- scraper has never stamped a success" >&2
    fail=1
    return
  fi
  ts=$(cat "$2" 2>/dev/null || echo 0)
  age=$(( (now - ts) / 86400 ))
  if [ "$age" -gt "$3" ]; then
    echo "FRESHNESS: $1 weekly scraper last SUCCEEDED ${age}d ago (max $3) -- check Windows Task Scheduler 'Last Result' on the residential box" >&2
    fail=1
  else
    echo "beacon $1: last success ${age}d ago (ok)"
  fi
}
check_beacon MN /home/private/docketdrift/.scrape_mn_last 9
check_beacon NH /home/private/docketdrift/.scrape_nh_last 9

# --- Are the PAGES still rendering? -----------------------------------------
# Everything above answers "is data arriving?" -- nothing above notices a page
# type that 500s on every request. On 2026-08-06 cited-by had been dead for 12
# days, plus three sitemap chunks and 117 dockets with no URL, while every
# check here reported green. Bolted on to this task rather than registered as
# its own, since the panel step is where monitors go to die.
/bin/sh /home/private/docketdrift/scripts/error_rate_check.sh || fail=1

exit $fail
