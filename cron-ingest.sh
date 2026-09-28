#!/bin/sh
# Ingestion cron for DocketDrift.
#
# Run by NFSN's scheduled tasks. Pulls the last 30 days of opinions from
# CourtListener into NFSN MariaDB. 30 days covers CL's ~month-long
# ingestion lag and gives slack for late-published rehearings;
# update_or_create in ingest_court makes re-fetching the same cluster a
# no-op, so overlapping windows across runs are safe.
#
# (Measured 2026-09-26: CL creates clusters the SAME DAY they are filed --
# lag 0 on 60 sampled -- so the 30-day window is not what loses opinions.
# What loses them is a run that dies part-way; see the failure handling
# below and CLAUDE.md 2026-09-26b.)
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
#
# FAILURE HANDLING (2026-09-27). This script used to run `set -e`, so ONE
# court that CourtListener throttled aborted every court after it plus the
# judge, span and IndexNow steps -- silently shrinking the week's ingest.
# Now every step runs, each failure is recorded, and the script exits
# non-zero at the END (so NFSN still emails) naming what failed. A failed
# ingest_court keeps whatever it wrote before failing; re-running it (or the
# next weekly run) picks up the rest.

cd /home/private/docketdrift

# FreeBSD `date -v-30d`; on Linux this would be `date -d "30 days ago"`.
SINCE=$(date -v-30d +%Y-%m-%d)

FAILED=""

# Run a step; on failure record it and carry on.
step() {
    if ! "$@"; then
        echo "!!! FAILED: $*"
        FAILED="$FAILED
  $*"
    fi
}

# Ingest one CL court, then its re-homing post-step (if it has one). The
# post-step runs even after a failed ingest: a partial run still landed rows,
# and they belong in the right circuit/division.
ingest_one() {
    cid=$1
    echo "--- ingesting $cid ---"
    step .venv/bin/python manage.py ingest_court "$cid" --since "$SINCE"

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
    if [ "$cid" = "lactapp" ]; then
        echo "--- re-homing LA COA circuits (since $SINCE) ---"
        step .venv/bin/python manage.py assign_la_circuits --apply \
            --since "$SINCE" --max-runtime 240
    fi

    # AZ COA post-step: the SAME shape as the LA circuits above. Both AZ
    # Court of Appeals divisions arrive down CourtListener's single
    # 'arizctapp' feed, so a fresh ingest lands every Division Two opinion
    # (dockets '2 CA-...') in Division One, the court that owns the CL id.
    #
    # No --since here (unlike LA): the command has no such flag and does not
    # need one -- AZ COA is ~24K rows, not LA's 341K, and a full dry-run
    # measures 1.5s. Idempotent; a correctly-homed opinion is a no-op.
    #
    # (Until 2026-09-27 both post-steps ran only in the single-court branch,
    # so the auto-discover path never re-homed anything.)
    if [ "$cid" = "arizctapp" ]; then
        echo "--- re-homing AZ COA divisions ---"
        step .venv/bin/python manage.py assign_az_divisions --apply
    fi
}

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest start, since=$SINCE, court=${1:-auto}"

if [ -n "$1" ]; then
    # Manual override: ingest only the specified court id (useful when
    # debugging a state's parser or rerunning a single court after a fix).
    ingest_one "$1"
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
        ingest_one "$cid"
    done
fi

# Attach judges to the new opinions. Nothing in the weekly path did this, and
# it went unnoticed for about six weeks (measured 2026-09-26): every state's
# opinions from mid-August on carried NO panel votes -- MN September 0/59,
# AZ 0/65, LA 0/63 -- so recent panels were blank and every judge's "last
# voted" date froze, which made seated judges look inactive on
# /current-judges/. Same $SINCE window as the ingest, which also sweeps up
# the MN/NH scraper ingests that don't pass through this script.
# Idempotent. No --create-missing: minting judges from bylines is an
# editorial step, not an unattended one.
STATES=$(.venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'docketdrift_site.settings')
django.setup()
from opinions.models import State
print(' '.join(State.objects.filter(is_live=True).order_by('code').values_list('code', flat=True)))
")
for st in $STATES; do
    echo "--- resolving judges ($st, since $SINCE) ---"
    step .venv/bin/python manage.py resolve_judges --state "$st" --since "$SINCE" --max-runtime 240
done

# Refresh the denormalized judge active-spans. These back /current-judges/
# and its era filters; they are derived from panel votes, so any ingest can
# move them. Cheap (seconds per state) and idempotent -- and NOT optional:
# computing the span live is what 500'd /current-judges/ on MN (2026-08-26),
# so the page now trusts these columns and a stale value shows a judge's
# tenure ending early. Runs unconditionally, including after a single-court
# manual run, because a new opinion in any court can extend a span.
echo "--- refreshing judge spans ---"
step .venv/bin/python manage.py backfill_judge_spans

# Hand the week's new opinion URLs to IndexNow (Bing et al.) instead of
# waiting for a sitemap re-crawl. The 200h window covers everything created
# since the last weekly run -- including the MN/NH scraper ingests, which
# don't pass through this script -- with overlap; re-pinging is harmless.
# Last on purpose: a rejected ping is recorded without having blocked the
# ingest or the span refresh above.
if [ -n "$(grep '^INDEXNOW_KEY=.' .env 2>/dev/null)" ]; then
    echo "--- IndexNow ping ---"
    step .venv/bin/python manage.py indexnow_ping --hours 200
fi

if [ -n "$FAILED" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest done WITH FAILURES:$FAILED"
    exit 1
fi
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cron-ingest done"
