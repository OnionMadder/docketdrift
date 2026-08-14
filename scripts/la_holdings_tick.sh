#!/bin/sh
# NFSN cull-safe wrapper for extract_holdings_text --state LA.
# The extractor SQL-excludes opinions that already have a
# holding_summary, but LA is dense with per-curiam orders that carry
# NO holding language -- so those empties never get flagged done, and
# without --min-id every tick re-scans them from pk=0 forever.
# Scrapes the last "resume with:  --min-id N" line and feeds it forward.
LOG=/home/logs/la-holdings.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  LAST=$(grep "resume with:  --min-id" "$LOG" 2>/dev/null | tail -1 | sed -E "s/.*--min-id ([0-9]+).*/\1/")
  MIN_ID=${LAST:-0}
  echo "=== [$(date +%FT%T)] holdings tick start min-id=$MIN_ID ===" >> "$LOG"
  python -u manage.py extract_holdings_text --state LA --max-runtime 480 --min-id "$MIN_ID" >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] holdings tick end rc=$? ===" >> "$LOG"
  sleep 15
done
