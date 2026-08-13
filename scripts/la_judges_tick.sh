#!/bin/sh
# NFSN cull-safe wrapper for resolve_judges --state LA.
# resolve_judges prints "resume the rest with:  --min-id N" (note "the
# rest" in the middle -- different wording than statutes/citations), so
# this uses a permissive grep that just extracts the --min-id number.
# --create-missing is ON: mints byline-learned Judge rows as UNKNOWN /
# is_currently_seated=False for editorial review, same as MN/NH/AZ did.
LOG=/home/logs/la-judges.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  LAST=$(grep -oE "resume.*--min-id [0-9]+" "$LOG" 2>/dev/null | tail -1 | grep -oE "[0-9]+$")
  MIN_ID=${LAST:-0}
  echo "=== [$(date +%FT%T)] judges tick start min-id=$MIN_ID ===" >> "$LOG"
  python -u manage.py resolve_judges --state LA --create-missing \
      --max-runtime 480 --min-id "$MIN_ID" --id-batch 20000 >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] judges tick end rc=$? ===" >> "$LOG"
  sleep 15
done
