#!/bin/sh
# Rotate the gunicorn daemon log safely.
#
# WHY THIS EXISTS
# ---------------
# Found 2026-09-08: /home/logs/daemon_gunicorn.log read 1.19 GB to `ls`
# and 121 MB to `du` -- a SPARSE file. Someone had truncated it in place
# (`> file`) while the writer held it open, so the writer kept appending
# at its old offset and left ~1 GB of NUL bytes in the middle.
#
# That is not cosmetic. grep and tail on the file returned data weeks
# older than the newest entries and cut off mid-line; a lifecycle grep
# reported the last worker boot as three weeks stale when the log was
# in fact current to the second. Anything diagnosing an incident from
# that log would have been reading fiction.
#
# NEVER TRUNCATE THIS FILE IN PLACE. run.sh passes gunicorn
# `--access-logfile -`, so gunicorn writes to STDOUT and NFSN's daemon
# supervisor owns the file descriptor. Truncating cannot make the
# supervisor seek back to zero -- only a respawn re-opens the path.
#
# So: MOVE the file (a move never creates a hole; the supervisor simply
# keeps writing to the moved inode), then restart the daemon so it
# opens a fresh file at the original path.
#
# Two scripts read this log -- scripts/error_rate_check.sh (tails 50K
# lines) and scripts/ai_citations.sh (weekly AI-usage digest). Both
# degrade gracefully to a short window and refill with traffic; at
# ~75-88K requests/day the 50K-line window rebuilds within a day.
#
# Usage:  sh scripts/rotate_access_log.sh [--dry-run]
set -u

LOG=/home/logs/daemon_gunicorn.log
KEEP_ARCHIVES=2
STAMP=$(date -u +%Y%m%d)
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

[ -f "$LOG" ] || { echo "no log at $LOG" >&2; exit 1; }

apparent=$(ls -l "$LOG" | awk '{print $5}')
ondisk=$(du -k "$LOG" | awk '{print $1 * 1024}')
printf 'current: apparent %.2f GB / on disk %.2f GB\n' \
    "$(echo "$apparent" | awk '{print $1/1073741824}')" \
    "$(echo "$ondisk" | awk '{print $1/1073741824}')"

if [ "$DRY" -eq 1 ]; then
    echo "(dry run -- would archive, rotate, and restart gunicorn)"
    exit 0
fi

# 1. Archive the REAL content, NULs stripped so the archive is usable
#    even when the source is sparse. Compressed, because it is text.
ARCHIVE="/home/logs/access-$STAMP.log.bz2"
echo "--- archiving real content to $ARCHIVE ---"
tr -d '\000' < "$LOG" | bzip2 -c > "$ARCHIVE" || {
    echo "ERROR: archive failed -- NOT rotating" >&2
    rm -f "$ARCHIVE"
    exit 1
}
[ -s "$ARCHIVE" ] || { echo "ERROR: archive is empty -- NOT rotating" >&2; exit 1; }
echo "archived $(du -h "$ARCHIVE" | cut -f1)"

# 2. Move it aside. NOT a truncate -- see the header.
echo "--- rotating ---"
mv "$LOG" "$LOG.rotating" || { echo "ERROR: mv failed" >&2; exit 1; }

# 3. Respawn so the supervisor opens a fresh file at the original path.
#    Until this lands, the daemon is still writing to the moved inode --
#    which is why the move has to be immediately followed by the restart.
nfsn -j signal-daemon gunicorn TERM || {
    echo "ERROR: restart failed -- restoring the log" >&2
    mv "$LOG.rotating" "$LOG"
    exit 1
}

# 4. Verify the supervisor actually re-opened the path. If it did not,
#    the site is still serving but nothing is being logged -- a silent
#    loss of the only monitoring surface, so this check is load-bearing.
n=0
while [ "$n" -lt 12 ]; do
    n=$(( n + 1 ))
    sleep 5
    [ -s "$LOG" ] && break
done

if [ -s "$LOG" ]; then
    newsize=$(ls -l "$LOG" | awk '{print $5}')
    echo "OK: new log open at $LOG ($newsize bytes and growing)"
    rm -f "$LOG.rotating"
else
    echo "WARNING: no new log after 60s. The supervisor may not have" >&2
    echo "re-opened the path. Old log kept at $LOG.rotating -- move it" >&2
    echo "back if logging does not resume." >&2
    exit 1
fi

# 5. Bound archive growth.
ls -1t /home/logs/access-*.log.bz2 2>/dev/null | tail -n +$(( KEEP_ARCHIVES + 1 )) \
    | while read -r old; do
        echo "pruning old archive: $old"
        rm -f "$old"
    done

echo "=== rotation complete ==="
