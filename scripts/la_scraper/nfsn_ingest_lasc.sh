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
# WHY IT WORKS IN CHUNKS
# ----------------------
# ingest_pdfs extracts text BEFORE it hashes and dedups, so a re-run
# re-parses every PDF it is pointed at -- and it has no --max-runtime.
# Pointing it at all ~10,700 documents at once would be culled partway
# (the MN backfill measured ~3,000 parses as past the limit and chunked at
# ~300), and the retry would re-parse from the top.
#
# So the manifest is walked in fixed slices: each slice gets its own
# directory, its own short-lived ingest process, and a stamp file when it
# completes. A killed run resumes at the first unstamped slice instead of
# starting over.
#
# Usage:
#   sh scripts/la_scraper/nfsn_ingest_lasc.sh /tmp/la_supreme.tsv /tmp/la_bf
#   CHUNK=250 sh scripts/la_scraper/nfsn_ingest_lasc.sh <manifest> <workdir>
#
# Manifest columns (TSV): stem, kind, release-date-text, url
set -u

MANIFEST=${1:?usage: nfsn_ingest_lasc.sh <manifest.tsv> <workdir>}
WORK=${2:?usage: nfsn_ingest_lasc.sh <manifest.tsv> <workdir>}
CHUNK=${CHUNK:-400}
BASE=/home/private/docketdrift
PYTHON="$BASE/.venv/bin/python"

[ -r "$MANIFEST" ] || { echo "cannot read manifest: $MANIFEST" >&2; exit 1; }
mkdir -p "$WORK" || exit 1
cd "$BASE" || exit 1

# A manifest written on Windows arrives CRLF, and the stray carriage return
# rides on the URL field and fails EVERY fetch -- invisibly, because a CR
# only returns the cursor, so the error line prints the URL looking
# perfectly correct. Normalize once, up front, rather than trusting the
# producer to have got it right.
CLEAN="$WORK/manifest.lf.tsv"
tr -d '\r' < "$MANIFEST" | grep . > "$CLEAN"

TOTAL=$(grep -c . "$CLEAN")
CHUNKS=$(( (TOTAL + CHUNK - 1) / CHUNK ))
echo "=== lasc ingest: $TOTAL row(s), $CHUNKS chunk(s) of $CHUNK ==="

i=0
while [ "$i" -lt "$CHUNKS" ]; do
    i=$(( i + 1 ))
    from=$(( (i - 1) * CHUNK + 1 ))
    to=$(( i * CHUNK ))
    stamp="$WORK/.done_$i"
    dir="$WORK/chunk_$i"

    if [ -f "$stamp" ]; then
        echo "--- chunk $i/$CHUNKS: already done, skipping"
        continue
    fi

    mkdir -p "$dir" || exit 1
    # An already-downloaded, non-empty file is not re-fetched, so a chunk
    # interrupted mid-download resumes rather than restarting.
    sed -n "${from},${to}p" "$CLEAN" | while IFS='	' read -r stem kind date url; do
        [ -n "${url:-}" ] || continue
        out="$dir/$stem.$kind.pdf"
        [ -s "$out" ] && continue
        curl -sSf -o "$out" -m 90 "$url" 2>/dev/null || {
            rm -f "$out"
            echo "  FETCH FAILED: $url" >&2
        }
    done

    have=$(ls -1 "$dir" 2>/dev/null | wc -l | tr -d ' ')
    want=$(sed -n "${from},${to}p" "$CLEAN" | grep -c .)

    if [ "$have" -eq 0 ]; then
        echo "ERROR: chunk $i downloaded nothing -- refusing to ingest" >&2
        exit 1
    fi

    echo "--- chunk $i/$CHUNKS: $have/$want pdf(s) -> ingesting"
    "$PYTHON" -u manage.py ingest_pdfs --dir "$dir" \
        --state LA --court supreme 2>&1 | tail -2
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "ERROR: chunk $i ingest failed rc=$rc (re-run to resume here)" >&2
        exit "$rc"
    fi
    date -u +%Y-%m-%dT%H:%M:%SZ > "$stamp"

    # Drop the downloaded PDFs as soon as they are ingested. ingest_pdfs
    # has already copied each one into media storage, so keeping the
    # working copy doubles the disk cost of the whole run for nothing --
    # and this account has a storage quota well below the filesystem's
    # free space. Holding all ~10,700 at once exhausted it mid-run and
    # took the WEBSITE DOWN: gunicorn could not write its cache
    # ("[Errno 69] Disc quota exceeded"). The stamp above is what makes
    # a chunk resumable, not the files.
    rm -rf "$dir"
done

echo "=== all $CHUNKS chunk(s) complete ==="
