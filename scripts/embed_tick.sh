#!/bin/sh
# DocketDrift embed tick -- one bounded embedding pass, driven by cron.
#
# Register this as an NFSN scheduled task (Manage Site -> Scheduled Tasks)
# running every ~10 minutes. That is the ENTIRE supervisor: NFSN's
# scheduler keeps invoking it, embed_opinions self-limits each pass to
# stay under NFSN's ~10-minute wallclock cull, and the next tick resumes
# via the indexed embedding_pending flag.
#
# There is deliberately NO loop, NO self-respawn, NO sentinel/exit-code
# handshake here. Those were the source of the silent-death bugs in the
# old _embed_<state>_loop.sh wrapper (which this replaces). What keeps the
# pipeline honest now:
#   - embed_opinions takes a single-flight flock, so an overrunning tick
#     never overlaps the next one.
#   - embed_opinions exits non-zero on any real failure; NFSN emails the
#     site owner on a non-zero scheduled-task exit. (Loud, not silent.)
#   - embed_opinions rewrites .embed_progress each batch; heartbeat.sh
#     alerts if that beacon goes stale while work remains.
#
# Target state lives in .embed_state (one USPS code, e.g. "AZ") so this
# script and the NFSN task entry never need editing to switch states:
#     echo AZ > /home/private/docketdrift/.embed_state   # start/switch
#     rm     /home/private/docketdrift/.embed_state       # stop after the
#                                                         # current pass
#
# Embedding is gated to an overnight window (see EMBED_TZ / EMBED_*_HOUR
# below) so it doesn't contend with daytime crawler traffic. Outside that
# window every tick is a no-op.
set -u

BASE=/home/private/docketdrift
STATE_FILE="$BASE/.embed_state"
PYTHON="$BASE/.venv/bin/python"
# Budget per pass. NFSN culls long daemons at ~10 min; 480s + one in-flight
# batch stays comfortably under that, and the flock makes a 10-min cadence
# safe even if a pass runs long.
MAX_RUNTIME=480

cd "$BASE" || exit 1

# --- overnight-only window -------------------------------------------------
# Embedding contends with the public site's DB (notably the per-opinion
# similar-opinions VEC scan that ClaudeBot triggers on every opinion page),
# so we only embed overnight in Onion's local time. The NFSN scheduled task
# keeps firing every ~10 min 24/7; OUTSIDE the window this is a near-instant
# no-op -- Django never even starts. A zoneinfo name keeps it DST-correct
# (America/Phoenix = Arizona, which doesn't observe DST anyway, so it's a
# fixed UTC-7). Window is [START, END) in EMBED_TZ. To run all day again,
# set EMBED_START_HOUR=0 and EMBED_END_HOUR=24. A manual run bypasses this
# gate entirely (it calls embed_opinions directly, not this script).
EMBED_TZ="America/Phoenix"
EMBED_START_HOUR=0     # inclusive (00:00 local)
EMBED_END_HOUR=6       # exclusive (06:00 local)

hour=$(TZ="$EMBED_TZ" date +%H)
hour=${hour#0}         # "08"/"09" -> "8"/"9" so [ ] doesn't choke on octal
if [ "$hour" -lt "$EMBED_START_HOUR" ] || [ "$hour" -ge "$EMBED_END_HOUR" ]; then
    exit 0
fi

# No state configured -> nothing to embed. Silent no-op (exit 0) so the
# scheduled task can stay registered between corpora.
if [ ! -s "$STATE_FILE" ]; then
    exit 0
fi

STATE=$(head -n 1 "$STATE_FILE" | tr -d '[:space:]')
if [ -z "$STATE" ]; then
    exit 0
fi

# exec so embed_opinions becomes the process NFSN tracks (clean exit code,
# no extra shell layer). Single-flight + resume are handled inside it.
exec "$PYTHON" -u manage.py embed_opinions --state "$STATE" --max-runtime "$MAX_RUNTIME"
