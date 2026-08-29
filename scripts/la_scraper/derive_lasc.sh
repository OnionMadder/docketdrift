#!/bin/sh
# Derived layers for freshly-backfilled LA Supreme rows.
#
# Each command is chunked against NFSN's CPU cull the way the LA phase-8
# loops were: bounded --max-runtime, resume from the printed
# "resume with:  --min-id N" trailer (DOUBLE space -- a single-space grep
# here once pinned every tick to --min-id 0 and cost a day).
#
# Usage:  sh scripts/la_scraper/derive_lasc.sh <min-id>
set -u

MIN=${1:?usage: derive_lasc.sh <min-id>}
BASE=/home/private/docketdrift
PY="$BASE/.venv/bin/python"
cd "$BASE" || exit 1

run_chunked() {
    cmd=$1
    cur=$MIN
    pass=0
    while [ "$pass" -lt 40 ]; do
        pass=$(( pass + 1 ))
        out=$("$PY" -u manage.py "$cmd" --state LA --min-id "$cur" \
              --max-runtime 300 2>&1)
        echo "$out" | tail -2
        next=$(echo "$out" | grep "resume with:  --min-id" | tail -1 \
               | sed -E 's/.*--min-id ([0-9]+).*/\1/')
        [ -z "$next" ] && break
        [ "$next" = "$cur" ] && break     # cursor stopped: done
        cur=$next
    done
    echo "=== $cmd done (cursor $cur) ==="
}

echo "### statutes ###"
run_chunked extract_statutes
echo "### holdings ###"
run_chunked extract_holdings_text
echo "### judges ###"
run_chunked resolve_judges
echo "### citations ###"
run_chunked extract_citations
echo "### judge spans ###"
"$PY" -u manage.py backfill_judge_spans 2>&1 | tail -2
echo "### ALL DERIVED PASSES COMPLETE ###"
