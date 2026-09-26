#!/bin/sh
# Latency / starvation monitor. Called from heartbeat.sh every tick, so it
# rides the already-registered `heartbeat` NFSN task -- deliberately NOT a
# new scheduled task (every task registered here has needed a member-panel
# step, and two sat silently broken for months behind one).
#
# WHY THIS EXISTS (2026-09-26): every incident that actually mattered through
# September 2026 was STARVATION, not errors --
#   2026-08-02  two concurrent searches -> one took 183s, site "down"
#   2026-08-26  32 concurrent MCP calls -> LA landing 0.02s -> 6.3s
#   2026-09-22  Applebot at 80x normal rate for two days
# In every case the site returned 200s, slowly. error_rate_check.sh counts
# 5xx and saw nothing; freshness_check.sh watches ingest and saw nothing;
# heartbeat's /healthz is a single SELECT 1 and stayed instant. A starved
# site is invisible to a monitor that only counts failures. This one reads
# the request DURATION that run.sh now writes as the last access-log field
# (%(M)s, milliseconds) and alerts when a page shape's median drifts.
#
# WHAT IT MEASURES: p50 and p95 per page shape over the last LINES lines
# (~90 min at ~6.5K lines/hour), plus the overall p50. Two triggers:
#   PRIMARY    a shape with >= SHAPE_MIN requests whose p50 exceeds its
#              threshold. Shapes have their OWN thresholds because a search
#              POST legitimately takes seconds while a landing page must not.
#   SECONDARY  overall p50 over OVERALL_P50_MS -- the starvation shape, where
#              EVERY page slows at once because the one worker is saturated.
# Medians, not means, because a handful of legitimately slow requests (a
# cold sitemap chunk, a common-term search) must not page anyone.
#
# ALERT ON TRANSITION, NOT ON STATE. heartbeat runs every 5-10 minutes; a
# monitor that exits 1 every tick for the duration of an incident sends a
# dozen identical emails and trains the reader to ignore them -- the exact
# failure that let cited-by 500 for twelve days. So this script keeps a state
# file and exits non-zero ONLY when the state CHANGES (OK -> SLOW: alert;
# SLOW -> OK: recovery notice) or when SLOW has persisted REALERT_HOURS
# without anyone acting. Every run appends one summary line to HISTORY, which
# is also the baseline data for re-tuning the thresholds below.
#
# A BLIND MONITOR MUST FAIL LOUD: unreadable log, zero parseable lines, or
# lines that carry no duration field (run.sh not deployed / format changed)
# all exit non-zero with a message saying the monitor is blind, never "ok".
#
# Privacy: the access log is query-stripped and network-truncated (run.sh);
# this reads path SHAPES and durations only. Nothing here can say who was
# slow, only which page type was.
#
# Tunables (env): DD_LAT_LINES, DD_LAT_SHAPE_MIN, DD_LAT_P50_MS,
#                 DD_LAT_OVERALL_P50_MS, DD_LAT_REALERT_HOURS
LOG=${DD_LAT_LOG:-/home/logs/daemon_gunicorn.log}
STATE=${DD_LAT_STATE:-/home/private/docketdrift/.latency_state}
HISTORY=${DD_LAT_HISTORY:-/home/logs/latency_check.log}

# ~90 minutes of traffic at September-2026 volume. Bounded so the scan stays
# far under NFSN's ~40s CPU cull -- a culled scan would be a false ALL CLEAR.
LINES=${DD_LAT_LINES:-10000}
# A shape needs this many timed requests before its median means anything.
SHAPE_MIN=${DD_LAT_SHAPE_MIN:-20}
# Default per-shape p50 threshold. Per-shape overrides live in the awk
# BEGIN block below, next to the shape definitions, so the two stay together.
P50_MS=${DD_LAT_P50_MS:-2000}
# Starvation net: median across ALL requests.
OVERALL_P50_MS=${DD_LAT_OVERALL_P50_MS:-1500}
# Re-send the alert if SLOW persists this long without a state change.
REALERT_HOURS=${DD_LAT_REALERT_HOURS:-6}

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

if [ ! -r "$LOG" ]; then
  echo "LATENCY: cannot read $LOG -- monitor is BLIND, not clear" >&2
  exit 1
fi

# Pass 1: parse -> "shape<TAB>ms" per timed request. Split on the quote
# character (paths carry spaces: AZ dockets like "1 CA-CV 25-0606 PB"); the
# duration is the first token after the closing UA quote. Lines from before
# the format change have nothing there and are skipped, not counted.
# Pass 2: sort by shape then numerically by ms, so percentiles are a
# positional read per group. FreeBSD awk has no sort, hence the pipe.
TAB="$(printf '\t')"
RESULT="$(
tail -n "$LINES" "$LOG" | awk -F'"' '
{
    req = $2                       # GET /some/path HTTP/1.1
    if (req !~ /^[A-Z]+ /) next
    split($NF, t, " ")             # last field: " 123" (ms); absent on pre-change lines
    ms = t[1]
    if (ms !~ /^[0-9]+$/) { untimed++; next }

    method = req; sub(/ .*/, "", method)
    path = req
    sub(/^[A-Z]+ /, "", path)
    sub(/ HTTP\/[0-9.]+$/, "", path)

    if (path ~ /^\/opinion\/.*\/cited-by\/?$/)   shape = "/opinion/<X>/cited-by/"
    else if (path ~ /^\/opinion\/.*\/pdf\/?$/)   shape = "/opinion/<X>/pdf/"
    else if (path ~ /^\/opinion\/./)             shape = "/opinion/<X>/"
    else if (path ~ /^\/opinions\/?$/)           shape = method ":/opinions/"
    else if (path ~ /^\/judge\/./)               shape = "/judge/<X>/"
    else if (path ~ /^\/compare\/judges\//)      shape = "/compare/judges/"
    else if (path ~ /^\/statute\/./)             shape = "/statute/<X>/"
    else if (path ~ /^\/rule\/./)                shape = "/rule/<X>/"
    else if (path ~ /^\/tag\/./)                 shape = "/tag/<X>/"
    else if (path ~ /^\/sitemap/)                shape = "/sitemap*.xml"
    else if (path ~ /^\/static\//)               shape = "/static/*"
    else if (path ~ /^\/mcp\/?$/)                shape = "/mcp"
    else if (path ~ /^\/admin\//)                shape = "/admin/*"
    else                                          shape = path

    printf "%s\t%s\n", shape, ms
    printf "__all__\t%s\n", ms     # duplicate into one global group for the overall p50
    timed++
}
END {
    # Side channel for the blindness check, sorted to the top by the tab.
    printf "\t__meta__\t%d\t%d\n", timed + 0, untimed + 0
}' | sort -t "$TAB" -k1,1 -k2,2n | awk -F'\t' \
    -v shapemin="$SHAPE_MIN" -v p50def="$P50_MS" -v overall50="$OVERALL_P50_MS" '
BEGIN {
    # Per-shape p50 thresholds (ms). Anything not listed uses p50def.
    # Search is a corpus-wide FULLTEXT candidate scan bounded at 12s and
    # ~10s is LEGITIMATE on a common term (CLAUDE.md, "NH is slowest
    # because smallest"); sitemap chunks are big index walks; /mcp mixes
    # 3ms lookups with multi-second searches. Everything a reader lands on
    # cold -- landing, opinion, judge, statute -- gets the tight default.
    thr["POST:/opinions/"] = 15000
    thr["/sitemap*.xml"]   = 15000
    thr["/mcp"]            = 8000
    thr["/admin/*"]        = 15000
    thr["/healthz"]        = 1000
    thr["/static/*"]       = 500
}
$1 == "" && $2 == "__meta__" { timed = $3; untimed = $4; next }
{
    s = $1; v = $2 + 0
    n[s]++; vals[s, n[s]] = v
}
END {
    if (timed + untimed == 0) {
        print "LATENCY: parsed 0 requests -- log format changed or log empty; monitor is BLIND" > "/dev/stderr"
        exit 3
    }
    if (timed == 0) {
        printf "LATENCY: %d requests in window, NONE carry a duration field -- is the run.sh with %%(M)s deployed and gunicorn restarted? monitor is BLIND\n", untimed > "/dev/stderr"
        exit 3
    }
    if (timed < shapemin) {
        printf "latency: only %d timed requests in window (%d untimed, pre-change lines); insufficient data, not alerting\n", timed, untimed
        exit 0
    }
    # The __all__ group is every request, already sorted by the pipe.
    N = n["__all__"]
    o50 = vals["__all__", int((N + 1) / 2)]
    i95 = int(N * 0.95); if (i95 < 1) i95 = 1
    o95 = vals["__all__", i95]
    delete n["__all__"]

    printf "latency: %d timed requests, overall p50 %dms p95 %dms\n", N, o50, o95
    print "  shape                          n     p50     p95   thr"
    slow = ""; nslow = 0
    for (s in n) {
        k = n[s]
        p50 = vals[s, int((k + 1) / 2)]
        i95 = int(k * 0.95); if (i95 < 1) i95 = 1
        p95 = vals[s, i95]
        t = (s in thr) ? thr[s] : p50def
        flag = ""
        if (k >= shapemin && p50 > t) { flag = "  SLOW"; slow = slow sprintf("%s (p50 %dms > %dms, n=%d) ", s, p50, t, k); nslow++ }
        printf "  %-28s %5d %7d %7d %5d%s\n", s, k, p50, p95, t, flag | "sort -k2 -rn"
    }
    close("sort -k2 -rn")
    if (nslow > 0) {
        printf "LATENCY: %d page shape(s) SLOW: %s-- medians, not outliers; the worker is likely saturated or a query plan flipped\n", nslow, slow > "/dev/stderr"
        exit 1
    }
    if (o50 > overall50) {
        printf "LATENCY: overall p50 %dms exceeds %dms with no single slow shape -- everything is slow at once; suspect worker starvation (crawler surge, MCP flood) or the DB\n", o50, overall50 > "/dev/stderr"
        exit 1
    }
    exit 0
}' 2>&1
)"
rc=$?
# awk inside a pipeline: its exit status is the pipeline's (last command), so
# rc is the second awk's. 0 = ok, 1 = slow, 3 = blind.

# One line of history per run, always -- this is the baseline for re-tuning.
first="$(printf '%s\n' "$RESULT" | head -n 1)"
printf '%s rc=%s %s\n' "$(stamp)" "$rc" "$first" >> "$HISTORY" 2>/dev/null

if [ "$rc" -eq 3 ]; then
  printf '%s\n' "$RESULT" >&2
  exit 1
fi

# --- transition logic --------------------------------------------------------
now=$(date +%s)
prev_state=OK; prev_ts=$now
if [ -r "$STATE" ]; then
  read -r prev_state prev_ts < "$STATE" 2>/dev/null
  case "$prev_ts" in ''|*[!0-9]*) prev_ts=$now ;; esac
  case "$prev_state" in OK|SLOW) ;; *) prev_state=OK ;; esac
fi

if [ "$rc" -eq 0 ]; then
  if [ "$prev_state" = "SLOW" ]; then
    printf 'OK %s\n' "$now" > "$STATE"
    echo "LATENCY RECOVERED after $(( (now - prev_ts) / 60 )) min -- back under thresholds" >&2
    printf '%s\n' "$RESULT" >&2
    exit 1   # non-zero so NFSN mails the recovery; it is a state change
  fi
  # Refresh the timestamp only on first OK, so "how long has it been fine" holds.
  [ -r "$STATE" ] || printf 'OK %s\n' "$now" > "$STATE"
  printf '%s\n' "$RESULT"
  exit 0
fi

# rc == 1: slow now.
if [ "$prev_state" = "OK" ]; then
  printf 'SLOW %s\n' "$now" > "$STATE"
  printf '%s\n' "$RESULT" >&2
  exit 1
fi
age_h=$(( (now - prev_ts) / 3600 ))
if [ "$age_h" -ge "$REALERT_HOURS" ]; then
  printf 'SLOW %s\n' "$now" > "$STATE"
  echo "LATENCY STILL SLOW -- ${age_h}h since first alert, re-sending" >&2
  printf '%s\n' "$RESULT" >&2
  exit 1
fi
# Still slow, already alerted, not yet time to re-alert: stay quiet.
printf '%s\n' "$RESULT"
exit 0
