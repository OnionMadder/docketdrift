#!/bin/sh
# error_rate_tick.sh -- the 5xx check on the HEARTBEAT, alerting on transition.
#
# error_rate_check.sh is the weekly report; on its own it found a broken
# page type at most a week late (cited-by 500'd for twelve days unseen,
# and /current-judges/ for a weekend). This wrapper runs the SAME check
# over roughly the last hour of the access log every heartbeat tick, and
# keeps a state file so it exits non-zero only when the state CHANGES --
# the latency_check.sh rule: a check that fails every tick for an
# incident's duration sends a dozen identical emails and trains the
# reader to delete them.
#
#   OK -> BROKEN      alert (exit 1)
#   BROKEN -> OK      recovery notice (exit 1: it is a state change)
#   BROKEN persists   re-alert every REALERT_HOURS
#   BLIND             (log unreadable / 0 requests parsed) exit 1 EVERY
#                     tick on purpose: a monitor that cannot see must not
#                     look green.
#
# Window: DD_ERR_TICK_LINES access-log lines (default 6000, ~1 hour at
# Sept-Oct 2026 volume). Thresholds come from error_rate_check.sh.

DIR=/home/private/docketdrift
STATE=${DD_ERR_STATE:-$DIR/.errrate_state}
REALERT_HOURS=${DD_ERR_REALERT_HOURS:-6}
LINES=${DD_ERR_TICK_LINES:-6000}

out=$(DD_ERR_LINES=$LINES /bin/sh "$DIR/scripts/error_rate_check.sh" 2>&1)
rc=$?
now=$(date +%s)

if [ $rc -ne 0 ] && [ $rc -ne 1 ]; then
  echo "ERROR-RATE TICK: check is BLIND (rc=$rc), not clear:" >&2
  echo "$out" >&2
  exit 1
fi

prev_state=OK; prev_ts=$now
if [ -r "$STATE" ]; then
  read -r prev_state prev_ts < "$STATE" 2>/dev/null
  case "$prev_state" in OK|BROKEN) ;; *) prev_state=OK ;; esac
  case "$prev_ts" in ''|*[!0-9]*) prev_ts=$now ;; esac
fi

if [ $rc -eq 0 ]; then
  if [ "$prev_state" = "BROKEN" ]; then
    printf 'OK %s\n' "$now" > "$STATE"
    echo "ERROR-RATE RECOVERED after $(( (now - prev_ts) / 60 )) min:" >&2
    echo "$out" >&2
    exit 1
  fi
  [ -r "$STATE" ] || printf 'OK %s\n' "$now" > "$STATE"
  exit 0
fi

# rc == 1: something is broken.
if [ "$prev_state" = "OK" ]; then
  printf 'BROKEN %s\n' "$now" > "$STATE"
  echo "$out" >&2
  exit 1
fi
age_h=$(( (now - prev_ts) / 3600 ))
if [ "$age_h" -ge "$REALERT_HOURS" ]; then
  printf 'BROKEN %s\n' "$now" > "$STATE"
  echo "ERROR-RATE STILL BROKEN -- ${age_h}h since first alert, re-sending:" >&2
  echo "$out" >&2
  exit 1
fi
exit 0
