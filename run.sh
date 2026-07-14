#!/bin/sh
# Run script for the DocketDrift gunicorn daemon on NFSN.
#
# Registered via the NFSN member panel under Sites -> Daemons. NFSN starts
# this process on site boot and restarts it on crash. Logs (access + error)
# go to stdout/stderr, which NFSN routes to the daemon log.
#
# Bound to 127.0.0.1:8000 -- NFSN's proxy site type forwards HTTP requests
# from the public web to this port.
#
# Tuning notes (2026-06-12 revision):
# - workers=1 + threads=8: single Python process to fit NFSN's shared-host
#   memory budget (each worker process is ~50-80MB Django; two workers were
#   pushing the daemon into NFSN's SILENT kill threshold -- no log, whole site
#   down). So we scale concurrency with THREADS, which share the one process's
#   heap (near-zero extra memory), NOT a second worker. Requests here are
#   DB-I/O-bound (waiting on MariaDB, which releases the GIL), so threads give
#   real concurrency for the read-mostly load. Bumped 4 -> 8 on 2026-07-14 for
#   more headroom + resilience (a slow query no longer soaks up 1-of-4 slots).
#   Watch the shared-MariaDB connection count if it ever misbehaves: each busy
#   thread can hold a pooled connection (CONN_MAX_AGE 30s), so 8 is the new peak.
#   Going to workers=2 would double memory and needs the NFSN process-RAM
#   allocation confirmed first (member panel) -- the silent-kill risk is real.
# - preload: imports Django + opinions app once in the master before
#   forking the worker, saving memory and speeding restarts.
# - max-requests 5000 + jitter 500: each worker auto-recycles after
#   roughly 4500-5500 requests. PRIOR setting was 200+jitter 50 -- with
#   workers=1 and sustained traffic (crawlers + heartbeat + precompute
#   cron + real users) that meant a recycle every 2-5 minutes, and
#   every recycle the next handful of requests pay cold-DB-connection
#   + cold-cache + cold-template-compile cost. The cyclical "sometimes
#   it loads instantly, sometimes it hangs for 15-20 seconds" pattern
#   on state pages was 100% this. Django doesn't actually leak enough
#   memory in normal operation to justify recycling every 200 requests;
#   25x raise means a typical recycle every ~75-90 minutes during busy
#   hours, basically invisible to real users.
# - timeout 60: gives slower paths (explore-tags context processor does
#   20 LIKE queries on raw_text) headroom past gunicorn's 30s default.
# - graceful-timeout 30: workers get half a minute to finish in-flight
#   requests when receiving SIGTERM (matches NFSN supervisor cycles).
#
# - access-logformat (2026-06-25): the DEFAULT gunicorn format logs %(r)s,
#   the FULL request line INCLUDING the query string, plus %(f)s (referer).
#   That persisted users' search terms (?q=...) into the daemon access log
#   -- exactly the discoverable artifact we refuse to keep ("Data is sacred"
#   in CLAUDE.md; lawyers fear discovery of their research trail). The custom
#   format below logs method + %(U)s (URL PATH ONLY, query stripped) + status
#   + bytes + user-agent (device/browser, which we do want), and OMITS both
#   the query string and the referer. The query never touches our log. The
#   companion change moves search itself to POST so the query isn't in the
#   URL in the first place (see views/templates). Keep these in lockstep.

cd /home/private/docketdrift
exec ./.venv/bin/gunicorn docketdrift_site.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers 1 \
    --threads 8 \
    --timeout 60 \
    --graceful-timeout 30 \
    --max-requests 5000 \
    --max-requests-jitter 500 \
    --preload \
    --access-logfile - \
    --error-logfile - \
    --access-logformat '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s "%(a)s"'
