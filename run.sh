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
# Tuning notes (2026-06-05):
# - workers=1 + threads=4: single Python process to fit NFSN's shared-host
#   memory budget (each worker process is ~50-80MB Django; two workers were
#   pushing the daemon into NFSN's silent kill threshold). Threads handle
#   the low concurrency of a beta read-mostly site fine.
# - preload: imports Django + opinions app once in the master before
#   forking the worker, saving memory and speeding restarts.
# - max-requests 200 + jitter 50: each worker auto-recycles after roughly
#   150-250 requests so any slow memory growth (template caching, etc.)
#   gets flushed before it accumulates.
# - timeout 60: gives slower paths (explore-tags context processor does
#   20 LIKE queries on raw_text) headroom past gunicorn's 30s default.
# - graceful-timeout 30: workers get half a minute to finish in-flight
#   requests when receiving SIGTERM (matches NFSN supervisor cycles).

cd /home/private/docketdrift
exec ./.venv/bin/gunicorn docketdrift_site.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers 1 \
    --threads 4 \
    --timeout 60 \
    --graceful-timeout 30 \
    --max-requests 200 \
    --max-requests-jitter 50 \
    --preload \
    --access-logfile - \
    --error-logfile -
