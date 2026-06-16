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
#   2. Embed progress: DURING the overnight embed window (EMBED_*_HOUR), if a
#      target state is configured and work remains, the .embed_progress beacon
#      embed_opinions rewrites each batch must be fresh. A stale beacon with
#      pending > 0 IN-WINDOW means the embed_tick task isn't running or its
#      passes are failing. OUTSIDE the window a stale beacon is EXPECTED
#      (embedding is intentionally paused) and is NOT alerted. (NFSN also
#      emails directly on a non-zero tick exit -- backstop for "not firing".)
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
# Only meaningful (a) when a target state is configured AND (b) INSIDE the
# overnight embed window. Outside the window a stale beacon is EXPECTED --
# embedding is intentionally paused -- so we must NOT alert on it. Keep
# EMBED_TZ / EMBED_*_HOUR in sync with scripts/embed_tick.sh.
EMBED_TZ="America/Phoenix"
EMBED_START_HOUR=0
EMBED_END_HOUR=6
if [ -s "$EMBED_STATE" ]; then
    ph_hour=$(TZ="$EMBED_TZ" date +%H); ph_hour=${ph_hour#0}
    if [ "$ph_hour" -ge "$EMBED_START_HOUR" ] && [ "$ph_hour" -lt "$EMBED_END_HOUR" ]; then
        target_state=$(head -n 1 "$EMBED_STATE" | tr -d '[:space:]')
        if [ ! -f "$EMBED_PROGRESS" ]; then
            echo "[$(stamp)] HEARTBEAT FAIL: .embed_state=$target_state but no $EMBED_PROGRESS beacon. Has the embed_tick scheduled task run at all?" >&2
            fail=1
        else
            # Beacon line is "<unix_ts> <pending_remaining>". Read pending
            # (field 2), forcing numeric so a partial write can't crash us.
            pending=$(awk '{print $2}' "$EMBED_PROGRESS" 2>/dev/null)
            case "$pending" in
                ''|*[!0-9]*) pending=0 ;;
            esac
            mtime=$(stat -f %m "$EMBED_PROGRESS" 2>/dev/null || stat -c %Y "$EMBED_PROGRESS" 2>/dev/null || echo "")
            if [ "$pending" -gt 0 ] && [ -n "$mtime" ]; then
                now=$(date +%s)
                # Measure staleness from the LATER of the beacon mtime and
                # today's window open, so a beacon left stale overnight doesn't
                # false-alert in the first minutes after the window opens
                # (before the night's first tick has refreshed it).
                window_open=$(TZ="$EMBED_TZ" date -v0H -v0M -v0S +%s 2>/dev/null || echo "")
                base=$mtime
                if [ -n "$window_open" ] && [ "$window_open" -gt "$mtime" ]; then
                    base=$window_open
                fi
                age_min=$(( (now - base) / 60 ))
                if [ "$age_min" -gt "$EMBED_STALL_MIN" ]; then
                    echo "[$(stamp)] HEARTBEAT FAIL: embed beacon stale ${age_min}m (threshold ${EMBED_STALL_MIN}m) with $pending opinion(s) pending for $target_state DURING the embed window. embed_tick isn't advancing -- check Scheduled Tasks + tail $EMBED_LOG." >&2
                    fail=1
                fi
            fi
        fi
    fi
fi

exit $fail
