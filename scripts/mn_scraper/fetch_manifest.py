"""Download the PDFs listed in a backfill manifest. Runs ON NFSN.

`backfill_mn_archive.py --no-download --manifest` writes a TSV of
`case<TAB>category<TAB>iso_date<TAB>url` from the residential browser. Only
mn.gov's *listing* is bot-walled -- the PDFs themselves serve fine to a
datacenter IP (verified: same byte size from NFSN and from a residential
connection), so the slow browser step only has to collect URLs and the bulk
download happens here.

Writes into `<out>/appeals/` and `<out>/supreme/` so each maps to one
`ingest_pdfs --court` run:

    python scripts/mn_scraper/fetch_manifest.py --manifest mn2020.tsv --out /tmp/mn2020
    python manage.py ingest_pdfs --dir /tmp/mn2020/appeals --state MN --court appeals
    python manage.py ingest_pdfs --dir /tmp/mn2020/supreme --state MN --court supreme

NFSN's ~10-minute wallclock cull applies, so this is BOUNDED and RESUMABLE:
`--max-runtime` self-exits cleanly and already-downloaded files are skipped, so
re-invoking picks up where it stopped. Exit code 2 means "more work remains"
(hit the time bound) -- 0 means the manifest is fully fetched.
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request

SUP_CATS = {"supct"}
UA = "DocketDrift/1.0 (+https://docketdrift.com; backfilling MN 2017-2023)"


def read_manifest(path):
    seen = set()
    rows = []
    with open(path, encoding="utf8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            case, cat, iso, url = parts[0], parts[1], parts[2], parts[3]
            if not case or not url or case in seen:
                continue
            seen.add(case)
            rows.append((case, cat, iso, url))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--delay", type=float, default=0.4,
                    help="Seconds between requests. Be polite -- this is a "
                         "state law library, not a CDN.")
    ap.add_argument("--max-runtime", type=int, default=480,
                    help="Self-exit after N seconds (NFSN cull is ~600). "
                         "0 = run to completion.")
    args = ap.parse_args()

    rows = read_manifest(args.manifest)
    started = time.time()
    got = skipped = failed = 0
    remaining = False

    for case, cat, iso, url in rows:
        sub = "supreme" if cat in SUP_CATS else "appeals"
        d = os.path.join(args.out, sub)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "%s.pdf" % case)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            skipped += 1
            continue
        if args.max_runtime and (time.time() - started) > args.max_runtime:
            remaining = True
            break
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
        except (urllib.error.URLError, OSError) as e:
            print("FAIL %s %s" % (case, e))
            failed += 1
            time.sleep(args.delay)
            continue
        if not body.startswith(b"%PDF"):
            # A bot-wall interstitial or an error page served as 200.
            print("NOT-A-PDF %s (%d bytes)" % (case, len(body)))
            failed += 1
            time.sleep(args.delay)
            continue
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
        got += 1
        time.sleep(args.delay)

    print("manifest=%d downloaded=%d already-had=%d failed=%d"
          % (len(rows), got, skipped, failed))
    if remaining:
        print("time bound hit -- re-run to continue (already-downloaded files "
              "are skipped).")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
