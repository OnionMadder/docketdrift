#!/bin/sh
# Run script for the DocketDrift gunicorn daemon on NFSN.
#
# Registered via the NFSN member panel under Sites -> Daemons. NFSN starts
# this process on site boot and restarts it on crash. Logs (access + error)
# go to stdout/stderr, which NFSN routes to the daemon log.
#
# Bound to 127.0.0.1:8000 -- NFSN's proxy site type forwards HTTP requests
# from the public web to this port.

cd /home/private/docketdrift
exec ./.venv/bin/gunicorn docketdrift_site.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers 2 \
    --access-logfile - \
    --error-logfile -
