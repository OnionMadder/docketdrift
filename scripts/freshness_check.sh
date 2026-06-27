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
set -e

cd /home/private/docketdrift
.venv/bin/python manage.py check_freshness
