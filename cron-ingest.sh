#!/bin/sh
# Ingestion cron for DocketDrift.
#
# Run by NFSN's scheduled tasks. Pulls the last 30 days of opinions from
# CourtListener into NFSN MariaDB. 30 days covers CL's ~month-long
# ingestion lag and gives slack for late-published rehearings;
# update_or_create in ingest_court makes re-fetching the same cluster a
# no-op, so overlapping windows across runs are safe.
#
# Auto-discovers which courts to ingest: any Court row whose State has
# is_live=True. Adding a new state to the database AND flipping its
# is_live flag is enough to put it on the weekly refresh schedule --
# no edit-this-shell-script step required. (Phase 12 of
# docs/STATE_ROLLOUT.md is silently complete the moment Phase 11 runs.)
#
# Usage:
#   ./cron-ingest.sh              # every CL court on every live state
#   ./cron-ingest.sh minn         # one specific CL court id (manual override)
#
# Logs (stdout + stderr) go to NFSN's scheduled-task log, viewable in the
# member panel under "Manage Scheduled Tasks".

set -e

cd /home/private/docketdrift

# FreeBSD `date -v-30d`; on Linux this would be `date -d "30 days ago"`.
SINCE=$(date -v-30d +%Y-%m-%d)

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest start, since=$SINCE, court=${1:-auto}"

if [ -n "$1" ]; then
    # Manual override: ingest only the specified court id (useful when
    # debugging a state's parser or rerunning a single court after a fix).
    .venv/bin/python manage.py ingest_court "$1" --since "$SINCE"

    # LA COA post-step: all five circuits arrive down CourtListener's single
    # 'lactapp' feed, so every ingest lands them in the landing court (First
    # Circuit -- the only circuit with a real CL id). Without this, circuits
    # 2-5 stay frozen at their last re-home and new opinions are attributed
    # to the wrong court. Verified 2026-08-19: of 21 freshly-ingested rows,
    # 4 were Second Circuit cases (e.g. 56,983-CA, Shreveport's comma-
    # numbered docket format) sitting in First Circuit.
    #
    # --since bounds it to the same window we just ingested, so this is a
    # few dozen rows, not a 341K re-scan. Idempotent: an opinion already in
    # its correct circuit is a no-op.
    if [ "$1" = "lactapp" ]; then
        echo "--- re-homing LA COA circuits (since $SINCE) ---"
        .venv/bin/python manage.py assign_la_circuits --apply \
            --since "$SINCE" --max-runtime 240
    fi

    # AZ COA post-step: the SAME shape as the LA circuits above. Both AZ
    # Court of Appeals divisions arrive down CourtListener's single
    # 'arizctapp' feed, so a fresh ingest lands every Division Two opinion
    # (dockets '2 CA-...') in Division One, the court that owns the CL id.
    # Left unchained for weeks as "filed, not fixed blind"; chained now that
    # AZ COA needed a measured catch-up (2026-08-26), rather than waiting for
    # a Div-2 opinion to show up misfiled on a live page.
    #
    # No --since here (unlike LA): the command has no such flag and does not
    # need one -- AZ COA is ~24K rows, not LA's 341K, and a full dry-run
    # measures 1.5s. Idempotent; a correctly-homed opinion is a no-op.
    if [ "$1" = "arizctapp" ]; then
        echo "--- re-homing AZ COA divisions ---"
        .venv/bin/python manage.py assign_az_divisions --apply
    fi
else
    # Auto-discover: every CL court id belonging to a live state, ordered
    # by state code then court level so logs read predictably across runs.
    COURT_IDS=$(.venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'docketdrift_site.settings')
django.setup()
from opinions.models import Court
for c in Court.objects.filter(state__is_live=True).order_by('state__code', 'level'):
    print(c.courtlistener_id)
")
    if [ -z "$COURT_IDS" ]; then
        echo "WARNING: no live courts found. Did you flip State.is_live=True?"
        exit 1
    fi
    for cid in $COURT_IDS; do
        echo "--- ingesting $cid ---"
        .venv/bin/python manage.py ingest_court "$cid" --since "$SINCE"
    done
fi

# Refresh the denormalized judge active-spans. These back /current-judges/
# and its era filters; they are derived from panel votes, so any ingest can
# move them. Cheap (seconds per state) and idempotent -- and NOT optional:
# computing the span live is what 500'd /current-judges/ on MN (2026-08-26),
# so the page now trusts these columns and a stale value shows a judge's
# tenure ending early. Runs unconditionally, including after a single-court
# manual run, because a new opinion in any court can extend a span.
echo "--- refreshing judge spans ---"
.venv/bin/python manage.py backfill_judge_spans

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest done"
