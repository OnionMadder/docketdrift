#!/bin/sh
# NFSN cull-safe wrapper for extract_holdings_text --state LA.
# No --max-runtime / --min-id; extractor SQL-excludes opinions that
# already have a holding_summary (unless --force). Each tick just
# re-invokes; culls lose only that batch.
LOG=/home/logs/la-holdings.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  echo "=== [$(date +%FT%T)] holdings tick start ===" >> "$LOG"
  python -u manage.py extract_holdings_text --state LA >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] holdings tick end rc=$? ===" >> "$LOG"
  sleep 15
done
