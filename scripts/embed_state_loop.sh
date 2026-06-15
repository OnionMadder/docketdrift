#!/bin/sh
# Self-respawning embed_opinions wrapper, parameterized by --state.
#
# This is the repo-committed source-of-truth for the on-NFSN wrappers
# at /home/private/docketdrift/_embed_<state>_loop.sh -- copy this file
# into place when bringing up the embed for a state, edit STATE=, and
# kick off via the standard nohup + disown pattern (see CLAUDE.md's
# deployment cheat sheet).
#
# Why a wrapper:
# - embed_opinions is resumable (WHERE embedding IS NULL) so re-running
#   after a kill picks up cleanly. NFSN's shell jail kills long-running
#   processes unpredictably; the wrapper just relaunches.
# - The wrapper exits cleanly when embed_opinions returns 0 (all done).
#
# Stability improvements vs the original 2026-06-09 wrapper:
# - PREFLIGHT manage.py check before the loop. If a deploy-blocking
#   system check fires (e.g. opinions.E001 catching a multi-line {# #}
#   comment), abort here instead of looping forever -- a previous bug
#   silently killed the embed for ~48 hours because every iteration
#   crashed during Django setup but the wrapper kept "restarting in
#   30s" without ever progressing.
# - RAPID-FAIL DETECTOR. If embed_opinions exits non-zero after
#   running for less than MIN_RUN_SECONDS, count it as a rapid fail.
#   After MAX_FAIL_STREAK rapid fails in a row, ABORT with a loud
#   stderr message. A long healthy run that ends in failure
#   (NFSN supervisor kill mid-run) resets the streak.
#
# Stop manually: pkill -f _embed_<state>_loop.sh
# Stop automatically: embed_opinions exits 0 -> wrapper breaks the loop.

STATE=NH
MIN_RUN_SECONDS=60
MAX_FAIL_STREAK=4
STATUS_FILE=/home/private/docketdrift/.embed_last_exit

# --skip-preflight (passed by the heartbeat supervisor on resurrection
# but NOT on first manual launch) makes us skip the manage.py check
# preflight. Each preflight costs ~30s and on NFSN's 10-minute wallclock
# cull policy we were paying that 30s every ~10 minutes of runtime --
# 5+ minutes per hour of preflight overhead at the steady-state
# resurrection rate. The supervisor only resurrects after seeing a
# previous instance crash with no rapid-fail brake firing, which means
# the last preflight passed; redundant.
SKIP_PREFLIGHT=0
if [ "${1:-}" = "--skip-preflight" ]; then
    SKIP_PREFLIGHT=1
fi

cd /home/private/docketdrift

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Write our final exit code to STATUS_FILE so the heartbeat supervisor
# can read it and decide whether to resurrect us, alert, or stand down.
#   0  -> all done, supervisor clears the marker
#   2  -> preflight failed, supervisor alerts (don't auto-restart)
#   3  -> rapid-fail brake fired, supervisor alerts (don't auto-restart)
#   99 -> "currently running" sentinel written at startup. If the
#         supervisor sees this with no live process, the wrapper got
#         SIGKILL'd (trap EXIT doesn't fire on SIGKILL) -- treat as
#         crash, resurrect.
#   any other -> normal crash / kill / NFSN supervisor cull, supervisor resurrects
write_status() { echo "$1" > "$STATUS_FILE"; }
write_status 99
trap 'write_status $?' EXIT

if [ $SKIP_PREFLIGHT -eq 0 ]; then
    echo "[$(stamp)] [wrapper] preflight: manage.py check"
    if ! /home/private/docketdrift/.venv/bin/python -u manage.py check; then
        echo "[$(stamp)] [wrapper] ABORT: manage.py check failed. Fix and relaunch." >&2
        exit 2
    fi
    echo "[$(stamp)] [wrapper] preflight ok. starting loop (state=$STATE)"
else
    echo "[$(stamp)] [wrapper] supervisor resurrect (preflight skipped). starting loop (state=$STATE)"
fi

fail_streak=0
while true; do
    echo "[$(stamp)] [wrapper] starting embed_opinions --state $STATE (fail_streak=$fail_streak)"
    run_start=$(date +%s)
    /home/private/docketdrift/.venv/bin/python -u manage.py embed_opinions --state "$STATE"
    exit_code=$?
    run_end=$(date +%s)
    run_duration=$((run_end - run_start))

    if [ $exit_code -eq 0 ]; then
        echo "[$(stamp)] [wrapper] embed_opinions exit 0 -- all $STATE opinions embedded. Done."
        exit 0
    fi

    if [ $run_duration -lt $MIN_RUN_SECONDS ]; then
        fail_streak=$((fail_streak + 1))
        echo "[$(stamp)] [wrapper] embed_opinions exit $exit_code after ${run_duration}s (rapid fail $fail_streak/$MAX_FAIL_STREAK)"
        if [ $fail_streak -ge $MAX_FAIL_STREAK ]; then
            echo "[$(stamp)] [wrapper] ABORT: $MAX_FAIL_STREAK rapid failures in a row. Something is broken -- investigate." >&2
            exit 3
        fi
    else
        fail_streak=0
        echo "[$(stamp)] [wrapper] embed_opinions exit $exit_code after ${run_duration}s. Restarting in 30s."
    fi
    sleep 30
done
