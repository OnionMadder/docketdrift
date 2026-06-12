#!/bin/sh
# DocketDrift heartbeat / health monitor.
#
# Designed to run as an NFSN scheduled task every 5 minutes. Performs
# three cheap checks and exits non-zero (with a loud stderr message,
# which NFSN's scheduled-task system delivers via email by default) if
# any of them fail:
#
#   1. /healthz responds 200 within 5s. Exercises Django + the
#      MariaDB connection pool end-to-end.
#   2. The embed wrapper process is alive (when one is supposed to be
#      running -- presence is determined by the marker file
#      /home/private/docketdrift/.embed_expected).
#   3. The embed log has advanced within the last EMBED_STALL_MIN
#      minutes (configurable below). Catches the "wrapper running but
#      child stuck" case.
#
# To enable embed monitoring: `touch /home/private/docketdrift/.embed_expected`
# To disable when the embed is intentionally idle: `rm` it.
#
# Stderr output is captured by NFSN's scheduled-task system and
# delivered to the site owner's email. Exit code 0 = quiet success.

set -u

HEALTHZ_URL="http://10.0.175.75:8000/healthz"
HEALTHZ_HOST="docketdrift.com"
EMBED_LOG="/home/logs/embed_opinions.log"
EMBED_MARKER="/home/private/docketdrift/.embed_expected"
EMBED_STALL_MIN=15

fail=0

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- (1) /healthz ----------------------------------------------------------
code=$(curl -sS --max-time 5 -o /tmp/healthz.body -w "%{http_code}" -H "Host: $HEALTHZ_HOST" "$HEALTHZ_URL" 2>/dev/null || echo "000")
if [ "$code" != "200" ]; then
    echo "[$(stamp)] HEARTBEAT FAIL: /healthz returned $code" >&2
    if [ -s /tmp/healthz.body ]; then
        echo "  body: $(head -c 300 /tmp/healthz.body)" >&2
    fi
    fail=1
fi

# --- (2) Embed wrapper alive (if expected) ---------------------------------
if [ -f "$EMBED_MARKER" ]; then
    if ! pgrep -f "_embed_nh_loop.sh" >/dev/null 2>&1; then
        echo "[$(stamp)] HEARTBEAT FAIL: $EMBED_MARKER exists but wrapper not running" >&2
        fail=1
    fi

# --- (3) Embed log advancing ----------------------------------------------
    if [ -f "$EMBED_LOG" ]; then
        mtime=$(stat -f %m "$EMBED_LOG" 2>/dev/null || stat -c %Y "$EMBED_LOG" 2>/dev/null || echo 0)
        now=$(date +%s)
        age_min=$(( (now - mtime) / 60 ))
        if [ "$age_min" -gt "$EMBED_STALL_MIN" ]; then
            echo "[$(stamp)] HEARTBEAT FAIL: $EMBED_LOG hasn't advanced in ${age_min}m (threshold ${EMBED_STALL_MIN}m)" >&2
            fail=1
        fi
    fi
fi

exit $fail
