#!/bin/sh
# NFSN cull-safe wrapper for extract_statutes --state LA.
# extract_statutes has no --min-id / --max-runtime -- it excludes already-
# extracted opinions at the SQL layer (opinions with any StatuteCitation
# row are skipped). Each tick just re-invokes; a cull mid-batch loses
# only that batch's uncommitted work, next tick picks up the rest.
LOG=/home/logs/la-statutes.log
cd /home/private/docketdrift
. .venv/bin/activate
while true; do
  echo "=== [$(date +%FT%T)] statutes tick start ===" >> "$LOG"
  python -u manage.py extract_statutes --state LA >> "$LOG" 2>&1
  echo "=== [$(date +%FT%T)] statutes tick end rc=$? ===" >> "$LOG"
  sleep 15
done
