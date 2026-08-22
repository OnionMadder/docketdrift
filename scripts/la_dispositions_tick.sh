#!/bin/sh
# ONE bounded disposition-backfill pass, driven by an NFSN scheduled task.
#
# Register as: /home/private/docketdrift/scripts/la_dispositions_tick.sh
# every ~10 minutes. That scheduled task IS the entire supervisor.
#
# WHY THIS IS NOT A LOOP (2026-08-21). This script used to be a `while
# true` wrapper launched with daemon(8). It worked, but NFSN's supervisor
# culls long-lived background processes, so it died roughly every few
# hours and only resumed when a human noticed and relaunched it -- three
# times in two days. A ~40-hour backfill cannot be babysat that way.
#
# The embed pipeline already solved this: a bounded single-pass command
# plus a scheduled task that keeps invoking it (see scripts/embed_tick.sh
# and the "NFSN's ~10-minute wallclock cull" gotcha in CLAUDE.md). A
# killed pass is harmless here -- the resume cursor lives in the log, and
# the next tick picks it up. Same design, applied to backfills.
#
# Copy this file for the next long backfill; only the CONFIG block changes.
set -u

# --- config -----------------------------------------------------------
BASE=/home/private/docketdrift
STATE=LA
COMMAND=backfill_dispositions
LOG=/home/logs/la-dispositions.log
LOCK="$BASE/.la_dispositions.lock"
DONE="$BASE/.la_dispositions_done"
MAX_RUNTIME=480          # self-exit under the ~10-min wallclock cull
PYTHON="$BASE/.venv/bin/python"
# ----------------------------------------------------------------------

cd "$BASE" || exit 1

# Corpus already swept: permanent, silent no-op. The task can stay
# registered forever; delete this file to force a full re-sweep (e.g.
# after a parser change that should revisit no-match rows).
[ -f "$DONE" ] && exit 0

# Single-flight. A pass that overruns must never overlap the next tick --
# two concurrent scans would duplicate work and double the DB load.
# lockf -t 0 fails immediately rather than queueing; EX_TEMPFAIL (75)
# just means the previous tick is still going, which is normal, not an
# error worth emailing about.
if [ -z "${DD_TICK_LOCKED:-}" ]; then
    DD_TICK_LOCKED=1
    export DD_TICK_LOCKED
    lockf -t 0 "$LOCK" "$0" "$@"
    rc=$?
    [ "$rc" -eq 75 ] && exit 0
    exit "$rc"
fi

# Resume cursor: the last "resume with:  --min-id N" the command printed
# (DOUBLE space -- the shared trailer format; a single-space grep here
# silently pins every tick to --min-id 0, which cost a day once).
LAST=$(grep "resume with:  --min-id" "$LOG" 2>/dev/null | tail -1 \
       | sed -E "s/.*--min-id ([0-9]+).*/\1/")
MIN_ID=${LAST:-0}

echo "=== [$(date -u +%Y-%m-%dT%H:%M:%SZ)] $COMMAND tick start min-id=$MIN_ID ===" >> "$LOG"
"$PYTHON" -u manage.py "$COMMAND" --state "$STATE" \
    --max-runtime "$MAX_RUNTIME" --min-id "$MIN_ID" >> "$LOG" 2>&1
rc=$?
echo "=== [$(date -u +%Y-%m-%dT%H:%M:%SZ)] $COMMAND tick end rc=$rc ===" >> "$LOG"

# A real failure exits non-zero so NFSN emails the owner. Loud, not silent.
[ "$rc" -ne 0 ] && exit "$rc"

# End-of-corpus detection: the cursor stopped advancing. Stamp DONE so
# every later tick is an instant no-op instead of re-walking the corpus
# forever (the idle-spin the statutes loop did for hours).
NEW=$(grep "resume with:  --min-id" "$LOG" 2>/dev/null | tail -1 \
      | sed -E "s/.*--min-id ([0-9]+).*/\1/")
NEW=${NEW:-0}
if [ "$NEW" = "$MIN_ID" ] && [ "$MIN_ID" != "0" ]; then
    date -u +%Y-%m-%dT%H:%M:%SZ > "$DONE"
    echo "=== corpus swept; stamped $DONE (delete it to re-sweep) ===" >> "$LOG"
fi

exit 0
