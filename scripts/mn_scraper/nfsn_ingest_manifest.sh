#!/bin/sh
# Fetch the manifest's PDFs on NFSN and ingest both MN courts.
#
# WHY THIS IS A SCRIPT AND NOT AN ssh ONE-LINER (2026-08-26). The weekly
# wrapper used to build this whole pipeline as a bash one-liner inside a
# PowerShell string. PowerShell mangles quotes when passing arguments to a
# native exe, so the inner quoting in
#
#     if [ -d "$d" ] && [ -n "$(ls -A $d)" ]
#
# reached bash unquoted: `ls -A` expanded to 101 filenames and test saw
# `[ -n file1 file2 ... ]` -> "[: too many arguments" -> the ingest branch
# never ran. Worse, the remote chain ended with `rm -rf`, which SUCCEEDS,
# so $LASTEXITCODE was 0: the wrapper logged "DONE", stamped the freshness
# beacon, and freshness_check reported healthy while 101 already-downloaded
# opinions were deleted un-ingested. Two weeks of MN filings were lost that
# way before a page sweep noticed the corpus was 14 days stale.
#
# Keeping the logic in a real shell file on the server means no PowerShell
# quoting layer, one exit code that actually means something, and failures
# that are loud.
#
#   nfsn_ingest_manifest.sh <manifest.tsv> <workdir>
set -eu

MANIFEST=${1:?usage: nfsn_ingest_manifest.sh <manifest> <workdir>}
WORKDIR=${2:?usage: nfsn_ingest_manifest.sh <manifest> <workdir>}
BASE=/home/private/docketdrift

cd "$BASE"
# shellcheck disable=SC1091
. .venv/bin/activate

rm -rf "$WORKDIR"
python scripts/mn_scraper/fetch_manifest.py \
    --manifest "$MANIFEST" --out "$WORKDIR" --max-runtime 0

ingested=0
for court in appeals supreme; do
    d="$WORKDIR/$court"
    [ -d "$d" ] || continue
    # Count files without word-splitting on names.
    n=$(find "$d" -type f -name '*.pdf' | wc -l | tr -d ' ')
    [ "$n" -gt 0 ] || continue
    echo "--- ingesting $n $court PDF(s) ---"
    # set -e makes a non-zero ingest abort the whole script, so a failure
    # here can never be masked by the cleanup below.
    python manage.py ingest_pdfs --dir "$d" --state MN --court "$court"
    ingested=$((ingested + n))
done

if [ "$ingested" -eq 0 ]; then
    echo "ERROR: manifest produced no PDFs to ingest" >&2
    exit 3
fi

rm -rf "$WORKDIR" "$MANIFEST"
echo "OK: ingested $ingested PDF(s)"
