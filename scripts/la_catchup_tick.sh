#!/bin/sh
# LA COA (cl_id=lactapp) freshness catch-up -- an OVERNIGHT job.
#
# CL rate-limits this hard. A burst of probes plus two concurrent ingests
# earned a 2,630-SECOND (44 minute) Retry-After on 2026-08-19. The client
# already sleeps the full Retry-After inside a pass (MAX_RETRIES_ON_429=3),
# so patience *within* a pass is handled. What this wrapper must NOT do is
# restart immediately after a pass gives up -- that just burns three more
# retries against a still-cooling limiter and can extend the cooldown.
#
# Hence the 30-MINUTE inter-pass sleep. ingest_court is idempotent on
# (court, case_number, release_date), so each pass resumes for free and
# re-fetches only what hasn't landed yet. 24 passes ~= 12h of coverage.
#
# NOTE: CL groups all five Louisiana COA circuits under the single id
# `lactapp`; our lactapp-2..5 court rows are synthetic. So this one feed
# carries every circuit, and assign_la_circuits sorts the new rows into
# divisions afterward.
#
# Logs DIRECTLY to the file -- never through `| tail`, which buffers until
# process exit and silently loses everything if the ssh session drops.
LOG=/home/logs/la-coa-catchup.log
cd /home/private/docketdrift || exit 1
. .venv/bin/activate
i=0
while [ $i -lt 24 ]; do
  i=$((i + 1))
  echo "=== [$(date +%FT%T)] lactapp pass $i ===" >> "$LOG"
  python -u manage.py ingest_court lactapp --since 2026-03-01 >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] pass $i end rc=$? ===" >> "$LOG"
  sleep 1800
done
echo "=== [$(date +%FT%T)] loop done after 24 passes ===" >> "$LOG"
