#!/bin/sh
# NFSN cull-safe wrapper for backfill_pages_from_cl --state NH.
# Reads the last "resume with: --min-id N" from the log, feeds it forward.
LOG=/home/logs/nh-pages.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  LAST=$(grep "resume with: --min-id" "$LOG" 2>/dev/null | tail -1 | sed -E "s/.*--min-id ([0-9]+).*/\1/")
  MIN_ID=${LAST:-0}
  echo "=== [$(date +%FT%T)] tick start min-id=$MIN_ID ===" >> "$LOG"
  python -u manage.py backfill_pages_from_cl --state NH --since 1980-01-01 \
      --max-runtime 480 --min-id "$MIN_ID" >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] tick end rc=$? ===" >> "$LOG"
  sleep 15
done
