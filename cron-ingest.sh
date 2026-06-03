#!/bin/sh
# Ingestion cron for DocketDrift.
#
# Run by NFSN's scheduled tasks. Pulls the last 30 days of opinions from
# CourtListener into NFSN MariaDB. 30 days covers CL's ~month-long
# ingestion lag and gives slack for late-published rehearings; update_or_create
# in ingest_court makes re-fetching the same cluster a no-op, so overlapping
# windows across runs are safe.
#
# Usage:
#   ./cron-ingest.sh              # both MN appellate courts
#   ./cron-ingest.sh minn         # MN Supreme only
#   ./cron-ingest.sh minnctapp    # MN Court of Appeals only
#
# Logs (stdout + stderr) go to NFSN's scheduled-task log, viewable in the
# member panel under "Manage Scheduled Tasks".

set -e

cd /home/private/docketdrift

# FreeBSD `date -v-30d`; on Linux this would be `date -d "30 days ago"`.
SINCE=$(date -v-30d +%Y-%m-%d)

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest start, since=$SINCE, court=${1:-all}"
if [ -z "$1" ]; then
    .venv/bin/python manage.py ingest_court minn      --since "$SINCE"
    .venv/bin/python manage.py ingest_court minnctapp --since "$SINCE"
else
    .venv/bin/python manage.py ingest_court "$1" --since "$SINCE"
fi
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest done"
