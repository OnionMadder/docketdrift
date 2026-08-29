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
    fails=0
    while [ "$pass" -lt 40 ]; do
        pass=$(( pass + 1 ))
        out=$("$PY" -u manage.py "$cmd" --state LA --min-id "$cur" \
              --max-runtime 300 2>&1)
        rc=$?

        # A FAILED pass is not a finished pass. The shared DB drops a
        # connection intermittently ("2013, Lost connection ... during
        # query"), and the first version of this loop read the missing
        # resume trailer as "done" and moved on -- holdings and judges
        # both reported complete having written nothing. Retry a failure;
        # only a clean run with a stalled cursor means finished.
        if [ "$rc" -ne 0 ]; then
            fails=$(( fails + 1 ))
            echo "  !! $cmd rc=$rc at --min-id $cur (retry $fails/3)"
            [ "$fails" -ge 3 ] && { echo "  !! giving up on $cmd" >&2; return 1; }
            sleep 20
            continue
        fi
        fails=0
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
