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

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest done"
