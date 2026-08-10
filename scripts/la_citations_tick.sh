#!/bin/sh
# NFSN cull-safe wrapper for extract_citations --state LA.
# extract_citations DOES support --min-id; each tick scrapes the last
# "resume with:  --min-id N" line from the log and passes it forward.
LOG=/home/logs/la-citations.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  LAST=$(grep "resume with:  --min-id" "$LOG" 2>/dev/null | tail -1 | sed -E "s/.*--min-id ([0-9]+).*/\1/")
  MIN_ID=${LAST:-0}
  echo "=== [$(date +%FT%T)] citations tick start min-id=$MIN_ID ===" >> "$LOG"
  python -u manage.py extract_citations --state LA --min-id "$MIN_ID" >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] citations tick end rc=$? ===" >> "$LOG"
  sleep 15
done
