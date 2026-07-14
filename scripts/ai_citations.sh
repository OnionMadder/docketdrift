#!/bin/sh
# ai_citations.sh -- digest of AI-agent traffic pulled from the gunicorn
# access log. Two buckets that mean very different things:
#
#   LIVE CITATIONS  -- a person asked an AI a question and it fetched a page
#                      to answer/cite RIGHT THEN: chatgpt-user, claude-user,
#                      claude-web, perplexity-user. This is the "we got cited"
#                      signal -- a human is mid-conversation and the model
#                      reached for a DocketDrift opinion as a source.
#   TRAINING/INDEX  -- bulk crawlers ingesting the corpus: gptbot, claudebot,
#                      meta-externalagent, ccbot, google-extended, perplexitybot,
#                      oai-searchbot, bytespider, amazonbot, applebot,
#                      anthropic-ai. Background, no human attached.
#
# Reads ONLY the access log, which is already query-stripped (path, no query
# string, no referer -- see run.sh). So it reports WHICH opinions AI agents
# fetch, never the questions behind them. Consistent with "data is sacred".
#
# Usage:  sh scripts/ai_citations.sh [DAYS]        (default 7)
# As an NFSN weekly scheduled task, its stdout is emailed to the account.

LOG="${DOCKETDRIFT_ACCESS_LOG:-/home/logs/daemon_gunicorn.log}"
DAYS="${1:-7}"
TODAY="$(date +%Y-%m-%d)"

if [ ! -r "$LOG" ]; then
    echo "ai_citations: access log not readable: $LOG" >&2
    exit 1
fi

# In-window date strings (dd/Mon/yyyy, matching the CLF access-log timestamp),
# built with BSD `date -v` (NFSN is FreeBSD).
DATES=""
i=0
while [ "$i" -lt "$DAYS" ]; do
    DATES="$DATES $(date -v-"${i}"d +%d/%b/%Y)"
    i=$((i + 1))
done

# One pass over the log: classify each request by User-Agent + date window,
# emit small TAB-tagged tallies. Sorting/formatting happens in the shell below
# so we avoid awk's print-to-pipe ordering pitfalls.
RAW="$(
    awk -v dates="$DATES" -F'"' '
    BEGIN {
        n = split(dates, a, " ");
        for (j = 1; j <= n; j++) want[a[j]] = 1;
        split("chatgpt-user claude-user claude-web perplexity-user", r, " ");
        for (j = 1; j <= 4; j++) ret[r[j]] = 1;
        split("gptbot oai-searchbot claudebot anthropic-ai perplexitybot ccbot google-extended meta-externalagent bytespider amazonbot applebot", t, " ");
        for (j = 1; j <= 11; j++) trn[t[j]] = 1;
    }
    {
        if (match($1, /\[[0-9]+\/[A-Za-z]+\/[0-9]+/) == 0) next;
        d = substr($1, RSTART + 1, RLENGTH - 1);
        if (!(d in want)) next;
        ua = tolower($4);
        split($2, rq, " "); p = rq[2];
        mr = ""; mt = "";
        for (k in ret) if (index(ua, k)) mr = k;
        if (mr == "") for (k in trn) if (index(ua, k)) mt = k;
        if (mr != "") { rc[mr]++; rtot++; if (p ~ /^\/opinion\//) rp[p]++; }
        else if (mt != "") { tc[mt]++; ttot++; }
    }
    END {
        for (k in rc) printf("RET\t%d\t%s\n", rc[k], k);
        for (p in rp) printf("RP\t%d\t%s\n", rp[p], p);
        for (k in tc) printf("TRN\t%d\t%s\n", tc[k], k);
        printf("RTOT\t%d\n", rtot + 0);
        printf("TTOT\t%d\n", ttot + 0);
    }
    ' "$LOG"
)"

rtot="$(printf '%s\n' "$RAW" | awk -F'\t' '$1=="RTOT"{print $2}')"
ttot="$(printf '%s\n' "$RAW" | awk -F'\t' '$1=="TTOT"{print $2}')"

echo "DocketDrift -- AI-agent traffic, last ${DAYS} days (through ${TODAY})"
echo "Source: gunicorn access log (paths only; no queries stored)"
echo

echo "LIVE CITATIONS  -- a person asked an AI, it fetched a page just then"
if [ "${rtot:-0}" -eq 0 ]; then
    echo "  (none yet -- this is the number to watch as AI grounding picks up)"
else
    printf '%s\n' "$RAW" | awk -F'\t' '$1=="RET"{printf "  %6d  %s\n",$2,$3}' | sort -rn
    echo   "  ------"
    printf "  %6d  TOTAL\n" "$rtot"
    echo
    echo "  Top opinions fetched by live agents:"
    printf '%s\n' "$RAW" | awk -F'\t' '$1=="RP"{printf "    %5d  %s\n",$2,$3}' | sort -rn | head -15
fi

echo
echo "TRAINING / INDEXING CRAWLERS  -- bulk ingest"
if [ "${ttot:-0}" -eq 0 ]; then
    echo "  (none in window)"
else
    printf '%s\n' "$RAW" | awk -F'\t' '$1=="TRN"{printf "  %6d  %s\n",$2,$3}' | sort -rn
    echo   "  ------"
    printf "  %6d  TOTAL\n" "$ttot"
fi
