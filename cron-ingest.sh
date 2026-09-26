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

# Re-home multi-panel courts. All five LA circuits arrive down CourtListener's
# single 'lactapp' feed and land in the First Circuit (the only circuit with
# a CL id); both AZ COA divisions arrive down 'arizctapp' and land in
# Division One. Until 2026-09-26 these steps ran ONLY in the manual
# single-court branch above, so the weekly auto run -- the one that actually
# executes -- never re-homed anything: Second Circuit opinions from
# 2026-08-26 were still sitting in the First Circuit a month later, with
# their judges' votes counted against the wrong court. Both are idempotent.
# LA is bounded to the ingest window (a full pass is a 341K re-scan); AZ COA
# is ~24K rows and a full pass measures 1.5s, and the command has no --since.
echo "--- re-homing LA COA circuits (since $SINCE) ---"
.venv/bin/python manage.py assign_la_circuits --apply --since "$SINCE" --max-runtime 240
echo "--- re-homing AZ COA divisions ---"
.venv/bin/python manage.py assign_az_divisions --apply

# Attach judges to the new opinions. Nothing in the weekly path did this, and
# it went unnoticed for about six weeks (measured 2026-09-26): every state's
# opinions from mid-August on carried NO panel votes -- MN September 0/59,
# AZ 0/65, LA 0/63 -- so recent panels were blank and every judge's "last
# voted" date froze, which made seated judges look inactive on
# /current-judges/. Same $SINCE window as the ingest, which also sweeps up
# the MN/NH scraper ingests that don't pass through this script.
# Idempotent. No --create-missing: minting judges from bylines is an
# editorial step, not an unattended one.
for st in $(.venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'docketdrift_site.settings')
django.setup()
from opinions.models import State
print(' '.join(State.objects.filter(is_live=True).order_by('code').values_list('code', flat=True)))
"); do
    echo "--- resolving judges ($st, since $SINCE) ---"
    .venv/bin/python manage.py resolve_judges --state "$st" --since "$SINCE" --max-runtime 240
done

# Refresh the denormalized judge active-spans. These back /current-judges/
# and its era filters; they are derived from panel votes, so any ingest can
# move them. Cheap (seconds per state) and idempotent -- and NOT optional:
# computing the span live is what 500'd /current-judges/ on MN (2026-08-26),
# so the page now trusts these columns and a stale value shows a judge's
# tenure ending early. Runs unconditionally, including after a single-court
# manual run, because a new opinion in any court can extend a span.
echo "--- refreshing judge spans ---"
.venv/bin/python manage.py backfill_judge_spans

# Hand the week's new opinion URLs to IndexNow (Bing et al.) instead of
# waiting for a sitemap re-crawl. The 200h window covers everything created
# since the last weekly run -- including the MN/NH scraper ingests, which
# don't pass through this script -- with overlap; re-pinging is harmless.
# Last on purpose: a rejected ping exits non-zero (NFSN emails) without
# having blocked the ingest or the span refresh above.
if [ -n "$(grep '^INDEXNOW_KEY=.' .env 2>/dev/null)" ]; then
    echo "--- IndexNow ping ---"
    .venv/bin/python manage.py indexnow_ping --hours 200
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest done"
