#!/bin/sh
# auto_promote.sh -- run NH + AZ ingests sequentially, then promote to live.
#
# Each ingest runs in the foreground of THIS script (which is itself
# nohup'd), one after another. Sequential keeps the CL token under its
# 5/min rate limit without exponential backoff -- parallel runs hammer
# the throttle and CL keeps escalating cooldowns to 40+ minutes.
#
# Failure handling: if any ingest exits nonzero, we abort the chain and
# log it. ingest_court is idempotent on (court, case_number) so a partial
# corpus from a failed run is recoverable by just re-running.
#
# Once all corpora are populated, runs:
#   (embedding) -- now handled by the overnight cron tick, see below
#   suggest_tags   (adds rows for new opinions; idempotent skip)
#   resolve_judges --state NH/AZ (no-op until rosters exist)
#   set is_live=True on State rows that have >0 opinions
#   nfsn signal-daemon gunicorn TERM  (so apex picker re-renders)
#
# Tail: tail -f /tmp/auto_promote.log

set -u  # NOTE: NOT set -e; we want to log + continue past tolerable failures
cd /home/private/docketdrift
. .venv/bin/activate

PROMOTE_LOG=/tmp/auto_promote.log
echo "[$(date)] auto_promote v2 started" > "$PROMOTE_LOG"

run_ingest() {
    NAME="$1"
    CL_ID="$2"
    SINCE="$3"
    LOG="/tmp/ingest_${NAME}.log"
    echo "[$(date)] === ingest $NAME ($CL_ID, since $SINCE) ===" >> "$PROMOTE_LOG"
    python manage.py ingest_court "$CL_ID" --since "$SINCE" > "$LOG" 2>&1
    RC=$?
    if [ $RC -eq 0 ]; then
        echo "[$(date)] $NAME ingest done (rc=0)" >> "$PROMOTE_LOG"
        return 0
    else
        echo "[$(date)] $NAME ingest FAILED (rc=$RC). Last log lines:" >> "$PROMOTE_LOG"
        tail -8 "$LOG" >> "$PROMOTE_LOG"
        return $RC
    fi
}

# Sequential ingests. NH first (smallest, fastest to feedback).
run_ingest nh         nh         2023-01-01
NH_RC=$?

run_ingest az_supreme ariz       2025-06-01
AZ_SUP_RC=$?

run_ingest az_appeals arizctapp  2025-06-01
AZ_APP_RC=$?

echo "[$(date)] all ingests done. rcs: nh=$NH_RC az_sup=$AZ_SUP_RC az_app=$AZ_APP_RC" >> "$PROMOTE_LOG"

# --- downstream pipeline runs regardless of individual ingest rc,
# so partial corpora still get embedded + tagged + promoted ---

# Embedding is NOT run inline anymore. Under the cron-tick model an
# unbounded inline `embed_opinions` would be SIGKILLed by NFSN's ~10-min
# wallclock cull and would contend with daytime traffic. Instead, point the
# overnight tick (scripts/embed_tick.sh) at the state that needs embedding;
# it works through the corpus in the 00:00-06:00 Phoenix window across as
# many nights as needed, resuming via the indexed embedding_pending flag.
# The tick is single-state, so for multiple new states set .embed_state to
# each in turn once the prior one finishes (beacon pending -> 0).
# NB: suggest_tags below scores opinions against embeddings, so it's only
# fully effective AFTER the overnight embed completes -- re-run
# `python manage.py suggest_tags` then to fill the suggestion queue.
echo "[$(date)] pointing overnight embed tick at AZ (.embed_state=AZ)..." >> "$PROMOTE_LOG"
echo AZ > /home/private/docketdrift/.embed_state

echo "[$(date)] suggest_tags..." >> "$PROMOTE_LOG"
python manage.py suggest_tags >> "$PROMOTE_LOG" 2>&1
echo "[$(date)] suggest_tags done (rc=$?)" >> "$PROMOTE_LOG"

echo "[$(date)] resolve_judges (no-op until rosters land)..." >> "$PROMOTE_LOG"
python manage.py resolve_judges --state NH >> "$PROMOTE_LOG" 2>&1 || true
python manage.py resolve_judges --state AZ >> "$PROMOTE_LOG" 2>&1 || true

echo "[$(date)] flip is_live=True on states with opinions..." >> "$PROMOTE_LOG"
python -c "
import django, os
os.environ.setdefault(\"DJANGO_SETTINGS_MODULE\", \"docketdrift_site.settings\")
django.setup()
from opinions.models import State, Opinion
for code in (\"NH\", \"AZ\"):
    s = State.objects.get(code=code)
    n = Opinion.objects.filter(court__state=s).count()
    if n > 0:
        s.is_live = True
        s.save(update_fields=[\"is_live\"])
        print(\"%s: is_live=True (%d opinions)\" % (code, n))
    else:
        print(\"%s: skipped (0 opinions)\" % code)
" >> "$PROMOTE_LOG" 2>&1

echo "[$(date)] restarting gunicorn..." >> "$PROMOTE_LOG"
nfsn signal-daemon gunicorn TERM >> "$PROMOTE_LOG" 2>&1

echo "[$(date)] auto_promote v2 DONE" >> "$PROMOTE_LOG"
