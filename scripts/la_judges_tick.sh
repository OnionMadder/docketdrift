#!/bin/sh
# NFSN cull-safe wrapper for resolve_judges --state LA.
# resolve_judges emits "resume the rest with:  --min-id N" on cull;
# --max-runtime 35 self-exits under NFSN's ~40s CPU cull;
# --id-batch 6000 controls the ordered id-list read (see AZ tuning).
LOG=/home/logs/la-judges.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  LAST=$(grep "resume the rest with:  --min-id" "$LOG" 2>/dev/null | tail -1 | sed -E "s/.*--min-id ([0-9]+).*/\1/")
  MIN_ID=${LAST:-0}
  echo "=== [$(date +%FT%T)] judges tick start min-id=$MIN_ID ===" >> "$LOG"
  python -u manage.py resolve_judges --state LA --create-missing \
      --max-runtime 35 --id-batch 6000 --min-id "$MIN_ID" >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] judges tick end rc=$? ===" >> "$LOG"
  sleep 5
done
