#!/bin/sh
# Download a lasc.org manifest and ingest it as LA Supreme opinions.
#
# Runs ON NFSN. The residential browser produced the manifest (the release
# pages are a Blazor SPA and need JS); the PDFs themselves are NOT walled
# from a datacenter IP -- verified 200 + application/pdf -- so the bulk
# fetch belongs here, where it is unattended and fast.
#
# THIS IS ONE COMMAND ON PURPOSE. The MN weekly scraper spent two weeks
# reporting success while ingesting nothing, because its remote step was a
# bash one-liner assembled inside a PowerShell string: the quoting mangled,
# the remote shell died, and $LASTEXITCODE still came back 0. A real script
# invoked as a single command is the fix -- see the gotcha in CLAUDE.md.
#
# Usage:
#   sh scripts/la_scraper/nfsn_ingest_lasc.sh /tmp/la2021.tsv /tmp/la2021
#
# Manifest columns (TSV): stem, kind, release-date-text, url
set -u

MANIFEST=${1:?usage: nfsn_ingest_lasc.sh <manifest.tsv> <workdir>}
WORK=${2:?usage: nfsn_ingest_lasc.sh <manifest.tsv> <workdir>}
BASE=/home/private/docketdrift
PYTHON="$BASE/.venv/bin/python"

[ -r "$MANIFEST" ] || { echo "cannot read manifest: $MANIFEST" >&2; exit 1; }

mkdir -p "$WORK/supreme" || exit 1
cd "$BASE" || exit 1

total=$(grep -c . "$MANIFEST")
echo "=== lasc ingest: $total row(s) from $MANIFEST ==="

# --- fetch ---------------------------------------------------------------
# Resumable by construction: an already-downloaded, non-empty file is
# skipped, so a killed run picks up where it stopped instead of re-pulling
# thousands of PDFs.
got=0; skipped=0; failed=0
while IFS='	' read -r stem kind date url; do
    [ -n "${url:-}" ] || continue
    out="$WORK/supreme/$stem.$kind.pdf"
    if [ -s "$out" ]; then
        skipped=$((skipped + 1))
        continue
    fi
    if curl -sSf -o "$out" -m 90 "$url" 2>/dev/null; then
        got=$((got + 1))
    else
        failed=$((failed + 1))
        rm -f "$out"
        echo "  FETCH FAILED: $url" >&2
    fi
done < "$MANIFEST"

echo "fetch: downloaded=$got already-had=$skipped failed=$failed"

# A failed fetch is worth surfacing but is not fatal on its own -- a single
# 404 among thousands should not abandon the ingest. A TOTAL failure is
# different and must not read as success.
if [ "$got" -eq 0 ] && [ "$skipped" -eq 0 ]; then
    echo "ERROR: nothing downloaded and nothing on disk -- refusing to ingest" >&2
    exit 1
fi

# --- ingest --------------------------------------------------------------
# ingest_pdfs dedups on (court, case_number, release_date), so re-running
# over an overlapping window is a no-op rather than a duplicate.
echo "--- ingesting $(ls -1 "$WORK/supreme" | wc -l | tr -d ' ') PDF(s) ---"
"$PYTHON" -u manage.py ingest_pdfs --dir "$WORK/supreme" \
    --state LA --court supreme
rc=$?
echo "=== ingest rc=$rc ==="
exit "$rc"
