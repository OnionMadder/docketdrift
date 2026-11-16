#!/bin/sh
# Derive the citation layers (statutes, citations, holdings, rules) for every
# opinion ingested since the last successful pass.
#
# WHY THIS IS NOT IN cron-ingest.sh ANY MORE (2026-11-16). The derive phase
# was added to the weekly cron on 2026-10-16, and from that week on the cron
# never finished: NFSN culls a scheduled task at ~10 minutes, and ingest +
# resolve_judges + derive does not fit -- extract_citations alone spends
# ~4.4 minutes building Louisiana's resolution map before scanning anything.
# The kill was SILENT (no "cursor HELD" line, no failure email): .derive_cursor
# sat at 489842 for a month, 203 new opinions got no citation layers, and the
# steps AFTER derive (judge spans, IndexNow) stopped running too -- every
# judge with a new vote showed a stale "last voted" date.
#
# Now: heartbeat.sh calls this every tick. Almost every tick is a ~2s no-op
# (cursor == newest pk). When there is new work it runs each step with
# --min-id at the cursor and advances the cursor ONLY if every step
# succeeded, so a failed or culled pass re-derives the same window next tick
# (every step is idempotent). Map caches live 168h; a cold LA rebuild costs
# ~4.4 min once a week and is written atomically, so even a pass culled after
# it is progress for the next one.
#
# Stamps .derive_last_ok on every successful pass (including no-ops);
# heartbeat.sh alerts when that stamp goes stale.
#
# A very large backlog (thousands of rows, e.g. after a bulk catch-up) may not
# fit in one tick; run the steps by hand in chunks for that, as before.
set -u

BASE=/home/private/docketdrift
LOCK="$BASE/.derive.lock"
CURSOR="$BASE/.derive_cursor"
OK_STAMP="$BASE/.derive_last_ok"
PY="$BASE/.venv/bin/python"

cd "$BASE" || exit 1

# Single-flight; 75 = a previous tick is still deriving, which is normal.
if [ -z "${DD_DERIVE_LOCKED:-}" ]; then
    DD_DERIVE_LOCKED=1
    export DD_DERIVE_LOCKED
    lockf -t 0 "$LOCK" "$0" "$@"
    rc=$?
    [ "$rc" -eq 75 ] && exit 0
    exit "$rc"
fi

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

FROM=$(cat "$CURSOR" 2>/dev/null)
case "$FROM" in
    ''|*[!0-9]*)
        echo "[$(stamp)] ERROR: $CURSOR missing or not a number -- nothing derived"
        exit 1
        ;;
esac

TOP=$("$PY" -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'docketdrift_site.settings')
django.setup()
from opinions.models import Opinion
print(Opinion.objects.order_by('-pk').values_list('pk', flat=True).first() or 0)
" 2>/dev/null)
case "$TOP" in
    ''|*[!0-9]*)
        echo "[$(stamp)] ERROR: could not read the newest opinion pk"
        exit 1
        ;;
esac

if [ "$TOP" -le "$FROM" ]; then
    date +%s > "$OK_STAMP"
    exit 0
fi

STATES=$("$PY" -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'docketdrift_site.settings')
django.setup()
from opinions.models import State
print(' '.join(State.objects.filter(is_live=True).order_by('code').values_list('code', flat=True)))
" 2>/dev/null)
if [ -z "$STATES" ]; then
    echo "[$(stamp)] ERROR: no live states found"
    exit 1
fi

echo "[$(stamp)] derive start: pk > $FROM (newest $TOP), states: $STATES"
FAILED=""
step() {
    if ! "$@" > /tmp/derive_step.$$ 2>&1; then
        echo "!!! FAILED: $*"
        tail -5 /tmp/derive_step.$$
        FAILED="$FAILED $3:$5"
    else
        tail -1 /tmp/derive_step.$$ | sed "s/^/  $3 $5: /"
    fi
    rm -f /tmp/derive_step.$$
}

for st in $STATES; do
    step "$PY" manage.py extract_statutes --state "$st" --min-id "$FROM"
    step "$PY" manage.py extract_citations --state "$st" --min-id "$FROM" \
        --map-cache "$BASE/.citemap.$st.json" --map-cache-hours 168
    step "$PY" manage.py extract_holdings_text --state "$st" --min-id "$FROM"
    step "$PY" manage.py extract_rules --state "$st" --min-id "$FROM"
done

if [ -n "$FAILED" ]; then
    echo "[$(stamp)] derive cursor HELD at $FROM (failed:$FAILED)"
    exit 1
fi
echo "$TOP" > "$CURSOR"
date +%s > "$OK_STAMP"
echo "[$(stamp)] derive cursor advanced $FROM -> $TOP"
