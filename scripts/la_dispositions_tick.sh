#!/bin/sh
# NFSN cull-safe wrapper for backfill_dispositions --state LA.
# Same pattern as la_statutes/la_holdings: --max-runtime self-exits under
# the wallclock cull; --min-id (scraped from the "resume with:  --min-id N"
# trailer, double space) advances past the no-match tail the disposition=''
# filter can't mark as done. Exits the loop when a tick reports no resume
# marker beyond the last one (end of corpus reached).
LOG=/home/logs/la-dispositions.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  LAST=$(grep "resume with:  --min-id" "$LOG" 2>/dev/null | tail -1 | sed -E "s/.*--min-id ([0-9]+).*/\1/")
  MIN_ID=${LAST:-0}
  echo "=== [$(date +%FT%T)] dispositions tick start min-id=$MIN_ID ===" >> "$LOG"
  python -u manage.py backfill_dispositions --state LA --max-runtime 480 --min-id "$MIN_ID" >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] dispositions tick end rc=$? ===" >> "$LOG"
  NEW=$(grep "resume with:  --min-id" "$LOG" 2>/dev/null | tail -1 | sed -E "s/.*--min-id ([0-9]+).*/\1/")
  if [ "$NEW" = "$MIN_ID" ]; then
    echo "=== [$(date +%FT%T)] no forward progress (corpus done); exiting ===" >> "$LOG"
    break
  fi
  sleep 15
done
