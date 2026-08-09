#!/bin/sh
# la_load_tick.sh — one bounded tick of the LA bulk load, cull-safe.
#
# Same shape as scripts/embed_tick.sh: a stateless one-shot that runs ONE
# ~8-minute pass of `load_cl_bulk` Phase 2b, then exits cleanly. Meant to
# be driven by an NFSN scheduled task (every ~10 min) OR a while-true bash
# loop on a residential machine, until Phase 2b reports 0 remaining
# opinions to fill.
#
# Uses --phase opinions-text so each tick jumps straight to the Phase 2b
# stream and doesn't burn 10 min re-running Phase 1 (judges) + Phase 2a
# (opinion metadata) each iteration. Phases 1 + 2a should be run once
# manually before starting the tick loop (they both fit under a single
# cull window at LA scale -- see the initial load session for timings).
# Phase 3 (panel votes) should be run once by hand after Phase 2b is
# complete -- ~1-5 min, well under a single cull.
#
# This wrapper is idempotent for Phase 2b -- re-running after all rows
# are filled is a fast no-op (the SELECT finds 0 rows and exits cleanly).
#
# NFSN scheduled task registration (member panel; not scriptable):
#   Tag:      la-load-tick
#   Command:  /bin/sh /home/private/docketdrift/scripts/la_load_tick.sh
#   Schedule: every 10 minutes (until the load is done)
#   Unregister the task when the log shows "nothing to do." twice in a row.
#
# Single-flight guard: flock() prevents overlap if the previous tick hasn't
# finished when the next one fires. Same pattern as embed_tick.sh.

set -e

DD_ROOT=/home/private/docketdrift
LOG=/home/logs/la-load.log
ERR=/home/logs/la-load.err
LOCK=/tmp/la_load_tick.lock
SUBSET=/home/private/courtlistener-bulk/la-subset
# 480s = 8 min of Phase-2b work, leaves ~2 min headroom under a 10-min cull
# after Django import + preflight COUNT overhead.
MAX_RUNTIME=480

# Refuse to run if the last tick is still going.
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "[$(date '+%F %T')] la_load_tick: previous tick still running, skipping." >> "$LOG"
    exit 0
fi

cd "$DD_ROOT"
. .venv/bin/activate

echo "" >> "$LOG"
echo "[$(date '+%F %T')] la_load_tick: starting (--max-runtime $MAX_RUNTIME)" >> "$LOG"

python -u manage.py load_cl_bulk \
    --subset-dir "$SUBSET" \
    --state LA \
    --phase opinions-text \
    --max-runtime "$MAX_RUNTIME" \
    >> "$LOG" 2>> "$ERR" \
    || {
        # Any non-zero exit is worth surfacing via NFSN's task-email so
        # a stuck load doesn't silently succeed forever.
        rc=$?
        echo "[$(date '+%F %T')] la_load_tick: FAILED rc=$rc (see la-load.err)" >&2
        exit $rc
    }

echo "[$(date '+%F %T')] la_load_tick: tick complete." >> "$LOG"
