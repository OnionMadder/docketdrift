#!/bin/sh
# Weekly 5xx monitor. Called by freshness_check.sh, so it rides the
# already-registered `freshnesscheck` NFSN task -- deliberately NOT a new
# scheduled task, because every task registered here so far has needed a
# member-panel step, and two of them sat silently broken for months.
#
# WHY THIS EXISTS (2026-08-06): /opinion/<docket>/cited-by/ hard-500'd on
# 1,844 pages for 12 days -- a bare .get() on a docket number, which is not
# unique once a case is appealed. It survived a full site audit and was found
# only because a crawler happened to trip it during an unrelated probe. The
# same scan then found three MORE broken page types nobody knew about: NH's
# only sitemap chunk, AZ's chunk 2, MN's chunk 3 (all timing out), and 117 AZ
# dockets whose URLs could not be generated at all.
#
# Every one of those was invisible to freshness_check, which watches whether
# DATA is arriving -- not whether PAGES still render. A page type can be
# 100% broken while every ingest pipeline is perfectly healthy.
#
# Privacy: the gunicorn access log is query-stripped by run.sh (path only, no
# query string, no referer), so this reports WHICH page types fail and never
# what anyone searched for. Paths are collapsed to shapes before counting, so
# no individual docket/judge is singled out in the alert either.
#
# Tunables (env): DD_ERR_LINES, DD_ERR_MIN, DD_ERR_RATE_PCT
LOG=/home/logs/daemon_gunicorn.log

# Bound the scan. NFSN culls a process at roughly 40s of CPU, and this log is
# large enough that a full grep gets killed partway -- which would fail as a
# false ALL CLEAR. tail keeps it to a fixed, cheap slice.
# ~1 day of traffic. The original 200000 spanned ~4 days, so a burst that
# was found AND FIXED kept re-alerting until it aged out of the window
# (seen 2026-08-07: Thursday's fixed cited-by burst still tripping Friday's
# check). The alert should describe breakage that exists NOW.
LINES=${DD_ERR_LINES:-50000}

# Alert if EITHER the absolute count or the rate crosses. The absolute floor
# catches a single page type dying (cited-by was ~117 per slice, well under
# 1% of traffic, so a rate-only rule would have stayed quiet).
MIN_5XX=${DD_ERR_MIN:-20}
RATE_PCT=${DD_ERR_RATE_PCT:-1}

if [ ! -r "$LOG" ]; then
  echo "ERROR-RATE: cannot read $LOG -- monitor is BLIND, not clear" >&2
  exit 1
fi

# One awk pass: parse, normalize to path shapes, tally. Splitting on the quote
# character survives paths containing spaces (AZ dockets: "1 CA-CV 25-0606 PB")
# and slashes (AZ election appeals: "CV-24-0222-AP/EL"), both of which a naive
# whitespace split truncates -- that mistake made this exact scan misreport
# "/opinion/1" as its own error class earlier today.
tail -n "$LINES" "$LOG" | awk -F'"' -v min5="$MIN_5XX" -v ratepct="$RATE_PCT" '
{
    req = $2                      # GET /some/path HTTP/1.1
    if (req !~ /^[A-Z]+ /) next
    rest = $3                     # " 500 145 "
    split(rest, a, " ")
    status = a[1]
    if (status !~ /^[0-9][0-9][0-9]$/) next

    total++
    if (status < 500 || status > 599) next
    err++

    path = req
    sub(/^[A-Z]+ /, "", path)
    sub(/ HTTP\/[0-9.]+$/, "", path)

    # collapse to a shape so one bad page type reads as one line, not 800
    if (path ~ /^\/opinion\/.*\/cited-by\/?$/)   shape = "/opinion/<X>/cited-by/"
    else if (path ~ /^\/opinion\/.*\/pdf\/?$/)   shape = "/opinion/<X>/pdf/"
    else if (path ~ /^\/opinion\/./)             shape = "/opinion/<X>/"
    else if (path ~ /^\/judge\/./)               shape = "/judge/<X>/"
    else if (path ~ /^\/statute\/./)             shape = "/statute/<X>/"
    else if (path ~ /^\/tag\/./)                 shape = "/tag/<X>/"
    else if (path ~ /^\/sitemap/)                shape = "/sitemap*.xml"
    else                                          shape = path
    count[shape]++
}
END {
    if (total == 0) {
        print "ERROR-RATE: parsed 0 requests -- log format may have changed; treating as FAILURE" > "/dev/stderr"
        exit 2
    }
    rate = err * 100.0 / total
    printf "error rate: %d 5xx / %d requests (%.3f%%) over last %d log lines\n", err, total, rate, NR
    if (err == 0) { print "no 5xx in window (ok)"; exit 0 }

    # always show the breakdown -- it is the actionable half of the alert
    print "  5xx by page shape:"
    for (s in count) printf "    %6d  %s\n", count[s], s | "sort -rn"
    close("sort -rn")

    if (err >= min5 || rate >= ratepct) {
        printf "ERROR-RATE: %d 5xx (%.3f%%) exceeds threshold (>=%d or >=%s%%) -- a page type is broken; see the breakdown above\n", err, rate, min5, ratepct > "/dev/stderr"
        exit 1
    }
    print "below alert threshold (ok)"
    exit 0
}'
