#!/bin/sh
# NFSN cull-safe wrapper for extract_statutes --state LA.
# extract_statutes supports --min-id + --max-runtime. LA's corpus is
# dense with short per-curiam orders that carry NO statute cites, so
# the SQL-layer done-exclusion alone can't skip them (only opinions
# WITH a StatuteCitation row are marked done). Without --min-id, every
# tick re-scans the same "empty" opinions from pk=0 → NFSN wallclock
# cull hits at ~200K rows deep → no forward progress. Each tick here
# scrapes the last "resume with:  --min-id N" line and feeds it forward.
LOG=/home/logs/la-statutes.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  LAST=$(grep "resume with:  --min-id" "$LOG" 2>/dev/null | tail -1 | sed -E "s/.*--min-id ([0-9]+).*/\1/")
  MIN_ID=${LAST:-0}
  echo "=== [$(date +%FT%T)] statutes tick start min-id=$MIN_ID ===" >> "$LOG"
  python -u manage.py extract_statutes --state LA --max-runtime 480 --min-id "$MIN_ID" >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] statutes tick end rc=$? ===" >> "$LOG"
  sleep 15
done
