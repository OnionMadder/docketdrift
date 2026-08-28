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
# WHAT CHANGED 2026-08-28 -- IT WAS CRYING WOLF. The original rule alerted on
# an absolute count of 20 5xx anywhere in the window. That was calibrated at
# three states and ~128K opinions; at four states and 469K it fires on
# ordinary background noise -- 1-3 stray 5xx per hour spread thinly across
# every page type, plus the residue of bugs that were already FIXED but had
# not yet aged out of the window. Every flagged page type was verified
# serving 200 by hand.
#
# That matters more than it sounds. A monitor that always fires is a monitor
# nobody reads, which is precisely how cited-by went unnoticed for twelve
# days. Raising the number would only buy time until the corpus grew again.
#
# So the trigger is now CONCENTRATION, which is the signal this was always
# trying to give: "a page TYPE is broken", not "some requests failed". A
# broken page type 500s on essentially every request to it, so it shows up as
# a high error RATE within its own shape. Diffuse noise never does.
#
# MEASURED against the real incidents this monitor exists for (the first
# draft of this used 20% and MISSED current-judges -- the numbers below are
# from the actual log, not from reasoning):
#   cited-by outage   ~117 errors on one shape, ~100% of that shape  -> FIRES
#   /current-judges/  15 errors / 83 requests to the shape = 18.1%   -> FIRES
#   2026-08-28 noise  worst shape 7 / 36,380 = 0.0%                  -> quiet
#
# WHY 10% AND NOT SOMETHING TIDIER LIKE 50%: a shape is shared across every
# state, because the access log records the PATH only and not the Host (that
# is deliberate -- see run.sh). /current-judges/ was hard-500ing on MN and
# serving fine on NH/AZ/LA, so a totally broken page could only ever reach
# ~25% of its shape, and landed at 18.1% once crawl distribution is factored
# in. One broken state out of N dilutes to roughly 1/N.
#
# THAT DILUTION GETS WORSE AS STATES ARE ADDED: at 8 states a single-state
# outage caps near 12%, which would slip under even this threshold. The
# durable fix is to put the Host into the access log so a shape concentrates
# per state -- privacy-neutral (it records WHICH subdomain, never who), but
# it changes the log format and the parser, so it is a deliberate follow-up
# rather than something to bolt on mid-incident. Revisit at state #5.
#
# Privacy: the gunicorn access log is query-stripped by run.sh (path only, no
# query string, no referer), so this reports WHICH page types fail and never
# what anyone searched for. Paths are collapsed to shapes before counting, so
# no individual docket/judge is singled out in the alert either.
#
# Tunables (env): DD_ERR_LINES, DD_ERR_SHAPE_MIN, DD_ERR_SHAPE_PCT,
#                 DD_ERR_RATE_PCT, DD_ERR_MIN
LOG=/home/logs/daemon_gunicorn.log

# Bound the scan. NFSN culls a process at roughly 40s of CPU, and this log is
# large enough that a full grep gets killed partway -- which would fail as a
# false ALL CLEAR. tail keeps it to a fixed, cheap slice.
# ~1 day of traffic. The original 200000 spanned ~4 days, so a burst that
# was found AND FIXED kept re-alerting until it aged out of the window
# (seen 2026-08-07: Thursday's fixed cited-by burst still tripping Friday's
# check). The alert should describe breakage that exists NOW.
LINES=${DD_ERR_LINES:-50000}

# PRIMARY TRIGGER: one page shape is broken. Both conditions must hold, and
# they are deliberately different kinds of check --
#   SHAPE_MIN  guards against a shape with 2 requests and 1 failure reading
#              as "100% broken" (a crawler hitting one malformed URL twice).
#   SHAPE_PCT  is what separates breakage from noise: a broken view fails
#              nearly every request, while background 5xx are a rounding
#              error against that shape's real traffic.
SHAPE_MIN=${DD_ERR_SHAPE_MIN:-10}
SHAPE_PCT=${DD_ERR_SHAPE_PCT:-10}

# SECONDARY TRIGGER: site-wide trouble that is spread too thin to concentrate
# in any single shape (DB down, worker saturation, cert expiry). Deliberately
# coarse -- this is the "everything is unwell" net, not the precise one.
RATE_PCT=${DD_ERR_RATE_PCT:-1}
MIN_5XX=${DD_ERR_MIN:-100}

if [ ! -r "$LOG" ]; then
  echo "ERROR-RATE: cannot read $LOG -- monitor is BLIND, not clear" >&2
  exit 1
fi

# One awk pass: parse, normalize to path shapes, tally per-shape totals AND
# per-shape errors. Splitting on the quote character survives paths containing
# spaces (AZ dockets: "1 CA-CV 25-0606 PB") and slashes (AZ election appeals:
# "CV-24-0222-AP/EL"), both of which a naive whitespace split truncates --
# that mistake made this exact scan misreport "/opinion/1" as its own error
# class once.
tail -n "$LINES" "$LOG" | awk -F'"' \
    -v shapemin="$SHAPE_MIN" -v shapepct="$SHAPE_PCT" \
    -v ratepct="$RATE_PCT" -v min5="$MIN_5XX" '
{
    req = $2                      # GET /some/path HTTP/1.1
    if (req !~ /^[A-Z]+ /) next
    rest = $3                     # " 500 145 "
    split(rest, a, " ")
    status = a[1]
    if (status !~ /^[0-9][0-9][0-9]$/) next

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

    total++
    shape_total[shape]++
    if (status < 500 || status > 599) next
    err++
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

    # Always show the breakdown -- it is the actionable half of the alert.
    # Each line carries the shape error rate, because that is the number the
    # decision is actually made on.
    print "  5xx by page shape (errors / requests to that shape):"
    for (s in count) {
        st = shape_total[s] ? shape_total[s] : count[s]
        printf "    %6d / %-7d  %5.1f%%  %s\n", count[s], st, count[s] * 100.0 / st, s | "sort -rn"
    }
    close("sort -rn")

    # PRIMARY: is any single page type broken?
    broken = ""
    nbroken = 0
    for (s in count) {
        st = shape_total[s] ? shape_total[s] : count[s]
        pct = count[s] * 100.0 / st
        if (count[s] >= shapemin && pct >= shapepct) {
            broken = broken sprintf("%s (%d/%d = %.0f%%) ", s, count[s], st, pct)
            nbroken++
        }
    }
    if (nbroken > 0) {
        printf "ERROR-RATE: %d page type(s) BROKEN: %s-- these fail most requests to them, which is breakage, not noise\n", nbroken, broken > "/dev/stderr"
        exit 1
    }

    # SECONDARY: everything unwell at once, too thin to concentrate.
    if (rate >= ratepct || err >= min5) {
        printf "ERROR-RATE: site-wide %d 5xx (%.3f%%) exceeds threshold (>=%d or >=%s%%) with no single broken page type -- suspect the DB, the worker, or the cert\n", err, rate, min5, ratepct > "/dev/stderr"
        exit 1
    }

    printf "no page type is broken (worst shape is background noise); %d scattered 5xx (ok)\n", err
    exit 0
}'
