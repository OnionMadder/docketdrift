"""Enumerate Louisiana Supreme Court opinions from lasc.org.

WHY THIS EXISTS
---------------
CourtListener's Louisiana Supreme feed is ~97% absent for 2020-2025:
measured in our own corpus, 2018 holds 2,232 opinions, 2021 and 2022 hold
ZERO, and 2023-2025 hold 11 / 42 / 49 against a ~2,000/yr norm. That is
the largest single-court hole in the country and it is the reason the LA
launch shipped with a coverage disclosure on /about/.

The opinions themselves are freely published at lasc.org. Only the
LISTING is awkward.

SHAPE (recon 2026-08-28, all measured against the live site)
------------------------------------------------------------
* The release calendar ``/courtactions/<YYYY>`` lists ~51 releases a
  year, each typed: ~38 "Actions", ~7 "Opinions", ~6 "Rehearings".
* A release page is ``/opinions?p=<YYYY>-<NNN>``. Direct URL construction
  WORKS -- which matters, because the site is Blazor Server over SignalR
  and the calendar rows carry no hrefs at all, so links cannot be
  harvested by reading the DOM.
* An "Opinions" release carries ~12 per-case PDFs; an "Actions" release
  carries ~70. So the volume lives in the Actions releases, and a year is
  roughly 2,700 documents.
* **The PDFs are NOT walled.** A plain datacenter curl gets 200 +
  application/pdf. The release-page HTML, by contrast, is a 14KB SPA
  shell with zero PDF links.

So this splits exactly like the MN archive backfill: a residential
browser LISTS (JS required), and NFSN DOWNLOADS (no wall, no browser).
That keeps the attended part to ~275 page loads instead of ~12,000.

WHAT IT EMITS
-------------
A manifest TSV, one row per PDF to fetch::

    <docket-stem>\\t<kind>\\t<release-date>\\t<url>

Feed it to scripts/la_scraper/nfsn_ingest_lasc.sh on the server, which
downloads and runs ``ingest_pdfs --state LA --court supreme``.

PRIMARY DOCUMENTS ONLY (v1)
---------------------------
One case on one date can carry several PDFs -- a per curiam plus a
justice's separate dissent (``21-1592.KK.PC.pdf`` and
``21-1592.KK.sjc.dis.pdf``). Opinion identity is
``(court, case_number, release_date)``, so those two collide on the
unique key. v1 keeps the PRIMARY document (OPN > PC > action) and skips
separate writings. The dissents are real data and worth a follow-up for
the judge layer -- they are a direct source of dissent votes, which is
where LA's panel coverage is weakest -- but they need a schema answer
first, not a bolt-on.

USAGE
-----
    python scripts/la_scraper/scrape_lasc.py --years 2020-2025 \\
        --manifest la_supreme.tsv

    python scripts/la_scraper/scrape_lasc.py --years 2021 --probe
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - local-only dependency
    sys.exit("playwright is required locally: pip install playwright\n"
             "NOTE: it must NEVER reach NFSN's FreeBSD (see CLAUDE.md).")

BASE = "https://www.lasc.org"

# Reuse the MN scraper's discipline: a persistent real-Chrome profile.
# lasc.org showed no bot wall in recon, but it sits behind Cloudflare, and
# a banked profile costs nothing while a challenge mid-sweep is expensive.
PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
    "docketdrift_la_scraper_profile",
)

# A lasc.org PDF path is "<docket>.<case-type>.<kind>[.<sub>].pdf":
#   /opinions/2021/20-0815.C.OPN.pdf        -> 20-0815  C   OPN
#   /opinions/2021/21-1196.C.action.pdf     -> 21-1196  C   action
#   /opinions/2021/21-1592.KK.sjc.dis.pdf   -> 21-1592  KK  sjc.dis  (a
#                                              justice's separate dissent)
# The case-type letter is part of the docket, NOT of the kind -- reading
# it as "C.OPN" made every document fail the primary check and the first
# full sweep emitted zero rows while reporting success.
PDF_HREF_RE = re.compile(
    r"^/opinions/(?P<year>\d{4})/(?P<docket>[0-9]{2}-[0-9]{3,5})"
    r"\.(?P<ctype>[A-Za-z]{1,3})\.(?P<kind>[A-Za-z0-9.]+)\.pdf$",
    re.IGNORECASE,
)

# Ranked: the document that IS the court's decision, best first. Anything
# not listed is a separate writing (dissent/concurrence) -- see the module
# docstring.
PRIMARY_KINDS = ("opn", "pc", "action")


def _release_rows(page, year: int) -> list[tuple[int, str, str]]:
    """Return (number, date_text, type) for every release in `year`."""
    page.goto("%s/courtactions/%d" % (BASE, year), wait_until="domcontentloaded")
    # Blazor renders the table after the socket connects; wait for content
    # rather than a fixed sleep (the MN lesson -- never networkidle, never
    # a guessed delay).
    page.wait_for_function(
        "() => document.querySelectorAll('tr').length > 3", timeout=30000)
    rows = page.evaluate(
        """() => [...document.querySelectorAll('tr')].map(tr =>
               [...tr.querySelectorAll('td')].map(td => td.textContent.trim())
           ).filter(c => c.length >= 3)"""
    )
    out = []
    for cells in rows:
        m = re.search(r"(\d{4})\s*-\s*(\d{1,3})", cells[0] or "")
        if not m or int(m.group(1)) != year:
            continue
        out.append((int(m.group(2)), cells[1] or "", cells[2] or ""))
    return sorted(out)


def _release_pdfs(page, year: int, number: int) -> list[dict]:
    """PDF links on one release page, primary documents only."""
    url = "%s/opinions?p=%d-%03d" % (BASE, year, number)
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_function(
            "() => [...document.querySelectorAll('a')]"
            ".some(a => (a.getAttribute('href')||'').toLowerCase()"
            ".endsWith('.pdf'))", timeout=20000)
    except Exception:
        # A release genuinely can carry no PDFs (a rehearing notice).
        # Report it rather than silently treating it as done.
        return []
    hrefs = page.evaluate(
        """() => [...document.querySelectorAll('a')]
               .map(a => a.getAttribute('href'))
               .filter(h => h && h.toLowerCase().endsWith('.pdf'))"""
    )

    # Keep the best primary document per case, drop separate writings.
    best: dict[str, dict] = {}
    for href in hrefs:
        m = PDF_HREF_RE.match(href)
        if not m:
            continue
        # The kind can carry a sub-segment ("sjc.dis"); only its FIRST
        # segment decides what the document is.
        kind = m.group("kind").lower().split(".")[0]
        if kind not in PRIMARY_KINDS:
            continue          # dissent/concurrence -- v1 skips
        stem = "%s.%s" % (m.group("docket"), m.group("ctype"))
        rank = PRIMARY_KINDS.index(kind)
        prev = best.get(stem)
        if prev is None or rank < prev["rank"]:
            best[stem] = {
                "stem": stem,
                "kind": kind,
                "rank": rank,
                "url": BASE + href,
            }
    return sorted(best.values(), key=lambda d: d["stem"])


def _parse_years(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", required=True,
                    help="A year (2021) or an inclusive range (2020-2025).")
    ap.add_argument("--manifest",
                    help="Write the TSV here (appended, so a crash resumes).")
    ap.add_argument("--pace", type=float, default=1.5,
                    help="Seconds between page loads (default 1.5).")
    ap.add_argument("--probe", action="store_true",
                    help="List the release calendar only; fetch no releases.")
    ap.add_argument("--headed", action="store_true",
                    help="Show the browser. Default is headless; use this if "
                         "Cloudflare ever starts challenging.")
    args = ap.parse_args()

    years = _parse_years(args.years)
    os.makedirs(PROFILE_DIR, exist_ok=True)

    # Resume support: never re-emit a URL the manifest already holds.
    seen: set[str] = set()
    if args.manifest and os.path.exists(args.manifest):
        with open(args.manifest, encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 4:
                    seen.add(parts[3])
        print("manifest already holds %d URL(s); they will be skipped"
              % len(seen))

    # newline is load-bearing: this manifest is read by a POSIX shell on
    # the server, and Python text mode on Windows writes CRLF. The stray
    # carriage return then rides on the URL field and every fetch fails --
    # invisibly, because a CR just returns the cursor, so the error line
    # prints the URL looking perfectly correct.
    out = (open(args.manifest, "a", encoding="utf-8", newline="\n")
           if args.manifest else None)
    total = 0
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE_DIR, channel="chrome", headless=not args.headed,
                viewport={"width": 1280, "height": 1000})
            page = ctx.new_page()
            for stale in list(ctx.pages):
                if stale is not page:
                    stale.close()

            for year in years:
                releases = _release_rows(page, year)
                kinds = {}
                for _, _, typ in releases:
                    kinds[typ] = kinds.get(typ, 0) + 1
                print("\n=== %d: %d releases (%s)" % (
                    year, len(releases),
                    ", ".join("%s %d" % (k, v) for k, v in sorted(kinds.items()))))
                if args.probe:
                    continue

                for number, date_text, typ in releases:
                    time.sleep(args.pace)
                    try:
                        pdfs = _release_pdfs(page, year, number)
                    except Exception as exc:
                        # Loud, and keep going: one bad release must not
                        # cost the whole year's sweep.
                        print("  !! %d-%03d (%s) FAILED: %s: %s" % (
                            year, number, typ, type(exc).__name__, exc))
                        continue
                    fresh = [d for d in pdfs if d["url"] not in seen]
                    for d in fresh:
                        seen.add(d["url"])
                        if out:
                            out.write("%s\t%s\t%s\t%s\n" % (
                                d["stem"], d["kind"], date_text, d["url"]))
                        total += 1
                    if out:
                        out.flush()
                    print("  %d-%03d %-11s %3d pdf(s)%s" % (
                        year, number, typ, len(pdfs),
                        "" if len(fresh) == len(pdfs)
                        else "  (%d new)" % len(fresh)))
            ctx.close()
    finally:
        if out:
            out.close()

    print("\n%d primary PDF(s) written to %s" % (total, args.manifest or "-"))
    print("Next: scp the manifest to the server, then\n"
          "  sh scripts/la_scraper/nfsn_ingest_lasc.sh <manifest> <workdir>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
