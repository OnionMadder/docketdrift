#!/bin/sh
# DocketDrift heartbeat / health + embed-stall monitor.
#
# Runs as an NFSN scheduled task every 5-10 minutes. Performs two cheap
# checks and exits non-zero (with a loud stderr message NFSN emails to the
# site owner) when a human is needed; exits 0 when all is well.
#
# NOTE (2026-06-15): this no longer SUPERVISES the embed. The embed is now
# driven directly by the NFSN scheduler via scripts/embed_tick.sh -- there
# is no resident wrapper to resurrect, no sentinel/exit-code handshake. The
# old self-respawning daemon model (and the silent-death bugs that came
# with it) is gone. This script's only embed job is to ALERT if the
# pipeline has silently stopped advancing.
#
# Checks performed:
#   1. /healthz responds 200. Exercises Django + the MariaDB pool.
#   2. Embed progress: if a target state is configured (.embed_state) and
#      work remains, the .embed_progress beacon embed_opinions rewrites
#      each batch must be fresh. A stale beacon with pending > 0 means the
#      embed_tick scheduled task isn't running or its passes are failing
#      (NFSN also emails directly on a non-zero tick exit -- this is the
#      backstop for "task not firing at all").
set -u

HEALTHZ_URL="http://10.0.175.75:8000/healthz"
HEALTHZ_HOST="docketdrift.com"
EMBED_LOG="/home/logs/embed_opinions.log"
EMBED_STATE="/home/private/docketdrift/.embed_state"
EMBED_PROGRESS="/home/private/docketdrift/.embed_progress"
# Beacon is rewritten every batch and a pass runs at most ~8 min, on a
# ~10-min cadence. 30 min (3 missed passes) means something is wrong, not
# just one slow/skipped tick.
EMBED_STALL_MIN=30

fail=0

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- (1) /healthz ----------------------------------------------------------
# Clear any stale body first so a current failure can't surface a PRIOR
# success body in its alert. --max-time 30 tolerates the brief DB-contention
# spike when an embed pass is running; /healthz itself is only a SELECT 1,
# so this is still tight enough to catch a real outage.
: > /tmp/healthz.body
code=$(curl -sS --max-time 30 -o /tmp/healthz.body -w "%{http_code}" -H "Host: $HEALTHZ_HOST" "$HEALTHZ_URL" 2>/dev/null || echo "000")
if [ "$code" != "200" ]; then
    echo "[$(stamp)] HEARTBEAT FAIL: /healthz returned $code" >&2
    if [ -s /tmp/healthz.body ]; then
        echo "  body: $(head -c 300 /tmp/healthz.body)" >&2
    fi
    fail=1
fi

# --- (2) Embed progress (cron-tick model) ---------------------------------
# Only meaningful when a state is configured for embedding.
if [ -s "$EMBED_STATE" ]; then
    target_state=$(head -n 1 "$EMBED_STATE" | tr -d '[:space:]')
    if [ ! -f "$EMBED_PROGRESS" ]; then
        echo "[$(stamp)] HEARTBEAT FAIL: .embed_state=$target_state but no $EMBED_PROGRESS beacon. Has the embed_tick scheduled task run at all?" >&2
        fail=1
    else
        # Beacon line is "<unix_ts> <pending_remaining>" written by
        # embed_opinions. Read the pending count (field 2), defaulting to 0
        # and forcing numeric so a malformed/partial write can't crash us.
        pending=$(awk '{print $2}' "$EMBED_PROGRESS" 2>/dev/null)
        case "$pending" in
            ''|*[!0-9]*) pending=0 ;;
        esac
        # Use the file mtime as "time of last completed batch". If BOTH stat
        # forms fail, leave mtime empty and SKIP the age check rather than
        # treating it as epoch 0 (which would fire a false stall alert).
        mtime=$(stat -f %m "$EMBED_PROGRESS" 2>/dev/null || stat -c %Y "$EMBED_PROGRESS" 2>/dev/null || echo "")
        if [ "$pending" -gt 0 ] && [ -n "$mtime" ]; then
            now=$(date +%s)
            age_min=$(( (now - mtime) / 60 ))
            if [ "$age_min" -gt "$EMBED_STALL_MIN" ]; then
                echo "[$(stamp)] HEARTBEAT FAIL: embed beacon stale ${age_min}m (threshold ${EMBED_STALL_MIN}m) with $pending opinion(s) still pending for $target_state. The embed_tick task isn't advancing -- check Scheduled Tasks + tail $EMBED_LOG." >&2
                fail=1
            fi
        fi
    fi
fi

exit $fail
