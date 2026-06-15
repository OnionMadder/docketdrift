#!/bin/sh
# DocketDrift heartbeat / health monitor / embed supervisor.
#
# Designed to run as an NFSN scheduled task every 5-10 minutes.
# Performs three cheap checks and -- for the embed wrapper case --
# ACTS as a supervisor that resurrects a crashed wrapper rather than
# just alerting. Exits non-zero (with a loud stderr message that NFSN
# emails to the site owner) on conditions that need a human; exits 0
# on conditions it auto-corrected.
#
# Checks performed:
#   1. /healthz responds 200 within 5s. Exercises Django + the
#      MariaDB connection pool end-to-end.
#   2. The embed wrapper process is alive (when one is supposed to
#      be -- presence is determined by the marker file
#      /home/private/docketdrift/.embed_expected). On a missing
#      wrapper, the supervisor reads the last exit code recorded by
#      the wrapper (.embed_last_exit) and:
#         exit 0  -> work is genuinely complete, supervisor REMOVES
#                    the marker, exits 0, no email.
#         exit 2  -> preflight (manage.py check) failed; the wrapper
#                    can't even start. ALERT (human must fix).
#         exit 3  -> rapid-fail brake fired (4 crashes in <60s each).
#                    ALERT (something fundamental is broken).
#         other   -> normal crash / kill / NFSN supervisor cull.
#                    RESURRECT the wrapper, exit 0, no email.
#   3. The embed log has advanced within the last EMBED_STALL_MIN
#      minutes. Catches "wrapper running but child stuck" -- alerts
#      without resurrecting (resurrection might mask a real stall).
#
# To start monitoring + auto-resurrecting an embed:
#   echo "_embed_az_loop.sh" > /home/private/docketdrift/.embed_expected
#   nohup /home/private/docketdrift/_embed_az_loop.sh \
#       >> /home/logs/embed_opinions.log 2>&1 < /dev/null & disown
#
# To stop:
#   rm /home/private/docketdrift/.embed_expected
#   pkill -f _embed_az_loop.sh   # if you want to kill it now too

set -u

HEALTHZ_URL="http://10.0.175.75:8000/healthz"
HEALTHZ_HOST="docketdrift.com"
EMBED_LOG="/home/logs/embed_opinions.log"
EMBED_MARKER="/home/private/docketdrift/.embed_expected"
EMBED_STATUS="/home/private/docketdrift/.embed_last_exit"
EMBED_STALL_MIN=15

fail=0

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- (1) /healthz ----------------------------------------------------------
# Clear any stale body from a previous heartbeat run -- otherwise a
# current curl failure would surface the PRIOR success body in its
# alert, which is misleading.
#
# --max-time 30s tolerates the gunicorn-worker DB-contention window
# during the FIRST ~30 min of an embed run on a low-coverage state:
# the WHERE embedding IS NULL batch fetch isn't selective yet and the
# scan competes with the public site for raw_text reads, briefly
# spiking /healthz response time. 5s was firing false alarms in that
# window; 30s waits past it (and is still tight enough to catch real
# outages since /healthz itself only does a SELECT 1).
: > /tmp/healthz.body
code=$(curl -sS --max-time 30 -o /tmp/healthz.body -w "%{http_code}" -H "Host: $HEALTHZ_HOST" "$HEALTHZ_URL" 2>/dev/null || echo "000")
if [ "$code" != "200" ]; then
    echo "[$(stamp)] HEARTBEAT FAIL: /healthz returned $code" >&2
    if [ -s /tmp/healthz.body ]; then
        echo "  body: $(head -c 300 /tmp/healthz.body)" >&2
    fi
    fail=1
fi

# --- (2) Embed wrapper supervision (if marker says we expect one) ---------
if [ -f "$EMBED_MARKER" ]; then
    wrapper_name=$(head -n 1 "$EMBED_MARKER" 2>/dev/null | tr -d '[:space:]')
    if [ -z "$wrapper_name" ]; then
        echo "[$(stamp)] HEARTBEAT FAIL: $EMBED_MARKER is empty (expected wrapper script name on line 1)" >&2
        fail=1
    elif ! pgrep -f "$wrapper_name" >/dev/null 2>&1; then
        # Wrapper isn't running. Decide whether to resurrect, alert, or
        # silently stand down based on the last recorded exit code.
        last_exit=0
        if [ -f "$EMBED_STATUS" ]; then
            last_exit=$(head -n 1 "$EMBED_STATUS" 2>/dev/null | tr -d '[:space:]')
            : "${last_exit:=0}"
        fi
        case "$last_exit" in
            0)
                # Work genuinely completed -- the wrapper exited cleanly.
                # Remove the marker so we stop monitoring; no alert.
                echo "[$(stamp)] [supervisor] $wrapper_name finished cleanly (exit 0). Removing marker."
                rm -f "$EMBED_MARKER"
                ;;
            2)
                echo "[$(stamp)] HEARTBEAT FAIL: $wrapper_name died with exit 2 (preflight check failed). Won't auto-restart. Run preflight + relaunch manually." >&2
                fail=1
                ;;
            3)
                echo "[$(stamp)] HEARTBEAT FAIL: $wrapper_name died with exit 3 (rapid-fail brake). Something is fundamentally broken; won't auto-restart. Tail /home/logs/embed_opinions.log to investigate." >&2
                fail=1
                ;;
            *)
                # Crash, kill, NFSN supervisor cull. Resurrect.
                # --skip-preflight tells the wrapper not to re-run
                # manage.py check -- the previous instance passed it,
                # and the supervisor only gets here after a non-brake
                # death, so the build is known-good. Saves ~30s/restart
                # on NFSN's ~10-min cull cycle.
                wrapper_path="/home/private/docketdrift/$wrapper_name"
                if [ -x "$wrapper_path" ]; then
                    echo "[$(stamp)] [supervisor] $wrapper_name died (last exit=$last_exit). Resurrecting."
                    nohup "$wrapper_path" --skip-preflight >> "$EMBED_LOG" 2>&1 < /dev/null & disown
                else
                    echo "[$(stamp)] HEARTBEAT FAIL: $wrapper_name marked expected but $wrapper_path is missing or not executable" >&2
                    fail=1
                fi
                ;;
        esac
    fi

    # --- (3) Embed log advancing ---------------------------------------
    # Only checked when a wrapper is supposed to be alive AND we didn't
    # just resurrect it (resurrection means the log will catch up soon).
    if pgrep -f "$wrapper_name" >/dev/null 2>&1 && [ -f "$EMBED_LOG" ]; then
        mtime=$(stat -f %m "$EMBED_LOG" 2>/dev/null || stat -c %Y "$EMBED_LOG" 2>/dev/null || echo 0)
        now=$(date +%s)
        age_min=$(( (now - mtime) / 60 ))
        if [ "$age_min" -gt "$EMBED_STALL_MIN" ]; then
            echo "[$(stamp)] HEARTBEAT FAIL: $EMBED_LOG hasn't advanced in ${age_min}m (threshold ${EMBED_STALL_MIN}m). $wrapper_name is alive but stuck -- DB / Voyage / network." >&2
            fail=1
        fi
    fi
fi

exit $fail
