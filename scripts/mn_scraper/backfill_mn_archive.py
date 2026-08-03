"""Date-windowed backfill of the MN appellate archive (2017-2023 gap).

WHY THIS EXISTS (and why it is not `scrape_mn_coa.py`)
-----------------------------------------------------
`scrape_mn_coa.py` does weekly FORWARD-fill: it walks the archive's
newest-first list from the top and stops once it pages past `--since`. It
cannot reach history -- there is no upper bound, and the pager window only
exposes pages 1-10 (~100 opinions). Reaching 2021 that way would mean paging
through every opinion since.

This script instead walks BACKWARD in bounded date windows, asking the search
for one window at a time so each result set is small enough to page through.

It also covers **supct**, which `scrape_mn_coa.py` deliberately skips. The
2020-2022 hole is in BOTH courts (MN Supreme published opinions are missing
too), so a COA-only backfill would leave half the gap open.

THE GAP IS COURTLISTENER'S, NOT OURS (measured 2026-08-03)
----------------------------------------------------------
CL's bulk export, our DB, and CL's live API all show MN 2020-2022 = zero for
both courts (control: minnctapp 2016 = 1,231). CL's dockets are empty for those
years too. So no CL path fixes this -- see CLAUDE.md. The opinions themselves
are freely available here, and the MN parser reads them with no changes.

THE ONE THING THAT WILL SILENTLY RUIN A RUN
-------------------------------------------
**`start-date` / `end-date` on the search URL are IGNORED.** Verified live:
a November-2021 window returned newest-first results. If we trusted them, every
window would return the same newest ~100 opinions and we would "successfully"
download 2026 opinions 300 times while reporting progress through 2017.

So this script NEVER trusts a window. `--probe` tries each candidate query
strategy once and REPORTS whether the returned dates actually fall inside the
requested window; the sweep then refuses to walk a window whose results don't
verify (see `window_is_honored`). Dates come from the PDF FILENAME, which
encodes them (`OP<case>-<mmddyy>.pdf`), so verification never depends on
scraping row text.

THE BOT WALL
------------
mn.gov's listing sits behind Radware. Observed 2026-08-03: a headed real-Chrome
load is clean, but the **second rapid programmatic navigation draws a CAPTCHA**.
Pacing matters more than anything else, so every navigation here is spaced by
`--pace` seconds (default 8) and a CAPTCHA is never solved automatically -- the
window is visible and a logged-on human clears it, exactly like the NH model.
The persistent profile banks that clearance.

PDFs are NOT walled (plain GET returns 200 application/pdf even from a
datacenter IP), so downloading is the easy half.

USAGE
-----
    # 1. Find out which query strategy actually bounds dates (SLOW ON PURPOSE).
    python scripts/mn_scraper/backfill_mn_archive.py --probe

    # 2. Sweep a window once a strategy is known to work.
    python scripts/mn_scraper/backfill_mn_archive.py \
        --strategy dateops --since 2021-01-01 --until 2021-12-31

    # 3. Ship + ingest (ingest_pdfs dedups on (court, case_number)).
    scp <out>/*.pdf docketdrift:/tmp/mnpdf/
    ssh ... manage.py ingest_pdfs --dir /tmp/mnpdf --state MN --court appeals
    ssh ... manage.py ingest_pdfs --dir /tmp/mnpdf --state MN --court supreme

Ingest is split by court because `ingest_pdfs` takes one `--court`; this script
writes COA and Supreme PDFs into separate subdirectories so the two runs are a
straight directory each.
"""
import argparse
import base64
import datetime
import os
import re
import sys
import tempfile
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SEARCH = "https://mn.gov/law-library/search/"
ARCHIVE = "https://mn.gov/law-library/search/opinions-archive.jsp"

# Shared with scrape_mn_coa.py on purpose: one banked bot-clearance, not two.
PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
    "docketdrift_mn_scraper_profile",
)

# COA categories + Supreme. supct is INCLUDED here (unlike scrape_mn_coa.py)
# because the 2020-2022 hole covers both courts.
COA_CATS = ("ctappub", "ctapun", "COAspectorders")
SUP_CATS = ("supct",)
ALL_CATS = COA_CATS + SUP_CATS
CAT_RE = re.compile(
    r"law-library-stat/archive/(%s)/" % "|".join(ALL_CATS), re.IGNORECASE)
ANY_ARCHIVE_SEL = "a[href*='law-library-stat/archive/']"

URL_FILTER = " OR ".join("url:/archive/%s" % c for c in ALL_CATS)

# Which weekdays the courts actually file on. COA = Monday, Supreme =
# Wednesday (verified against the index; Tue/Thu/Fri probed and empty).
# Mutated from --weekdays at startup so build_url and iter_windows agree.
FILING_WEEKDAYS = {0, 2}

# In-page same-origin fetch. Playwright's request API is fingerprinted and
# blocked; a fetch() from the passing page context is not. (The NH lesson.)
FETCH_JS = """
async (url) => {
  try {
    const r = await fetch(url, {credentials: 'include'});
    if (!r.ok) return {ok:false, status:r.status};
    const bytes = new Uint8Array(await r.arrayBuffer());
    let bin = ''; const chunk = 8192;
    for (let i=0;i<bytes.length;i+=chunk){
      bin += String.fromCharCode.apply(null, bytes.subarray(i,i+chunk));
    }
    return {ok:true, status:r.status, b64: btoa(bin)};
  } catch (e) { return {ok:false, err: String(e)}; }
}
"""

# OPa210414-112221.pdf  -> case a210414, date 11/22/21
# OPA231400-07152026.pdf-> case A231400, date 07/15/2026 (4-digit year form)
# The trailing [^/]* matters: real filenames carry suffixes, e.g.
# `OPa190959-040620%20Revised.pdf`. Without it those parse as None, get no
# date, and drag the whole window into "cannot verify" and get skipped.
FNAME_RE = re.compile(
    r"OP([A-Za-z]?\d+)-(\d{2})(\d{2})(\d{4}|\d{2})[^/]*\.pdf$", re.IGNORECASE)
# Older scheme, still served for COAspectorders: a231903.pdf (no date).
BARE_RE = re.compile(r"/([A-Za-z]\d{6,})\.pdf$", re.IGNORECASE)


def parse_pdf_url(href):
    """-> (case_number, iso_date_or_None, category). Date comes from the
    FILENAME, so window verification never depends on page text."""
    cat = None
    m = CAT_RE.search(href)
    if m:
        cat = m.group(1).lower()
    fn = FNAME_RE.search(href)
    if fn:
        raw, mm, dd, yy = fn.group(1), fn.group(2), fn.group(3), fn.group(4)
        year = int(yy) if len(yy) == 4 else 2000 + int(yy)
        try:
            iso = datetime.date(year, int(mm), int(dd)).isoformat()
        except ValueError:
            iso = None
        return normalize_case(raw), iso, cat
    b = BARE_RE.search(href)
    if b:
        return normalize_case(b.group(1)), None, cat
    return None, None, cat


def normalize_case(raw):
    """a210414 -> A21-0414. Leaves anything unexpected alone."""
    raw = raw.strip()
    m = re.match(r"^([A-Za-z])(\d{2})(\d{3,4})$", raw)
    if not m:
        return raw.upper()
    return "%s%s-%s" % (m.group(1).upper(), m.group(2), m.group(3).zfill(4))


def build_url(strategy, start, end):
    """Candidate ways to ask for a date window. `getform` is known BROKEN
    (dates ignored) and is kept only so --probe can demonstrate that."""
    base = {
        "case": "", "docket": "", "qt": "", "sortby": "",
        "v:sources": "mn-law-library-opinions",
    }
    if strategy == "getform":
        p = dict(base, query="(%s)" % URL_FILTER,
                 **{"start-date": start.strftime("%m/%d/%Y"),
                    "end-date": end.strftime("%m/%d/%Y")})
        return SEARCH + "?" + urlencode(p)
    if strategy == "dateops":
        q = "(%s) date:>%s date:<%s" % (URL_FILTER, start.isoformat(),
                                        end.isoformat())
        return SEARCH + "?" + urlencode(dict(base, query=q,
                                             **{"start-date": "", "end-date": ""}))
    if strategy == "daterange":
        q = "(%s) date:[%s TO %s]" % (URL_FILTER, start.isoformat(),
                                      end.isoformat())
        return SEARCH + "?" + urlencode(dict(base, query=q,
                                             **{"start-date": "", "end-date": ""}))
    if strategy in ("filedate", "filedate_bare"):
        # The filename encodes the filing date: OP<case>-<mmddyy>.pdf. MN files
        # opinions on Mondays, so one query per Monday is ~30-40 results -- well
        # inside the ~10-page pager, and trivially verifiable against the window.
        # TWO stamp forms, and both are required. COA files are
        # OP<case>-<mmddyy>.pdf (6-digit) but Supreme files are
        # OP<case>-<mmddyyyy>.pdf (4-digit year). The index tokenizes the URL,
        # so a 6-digit stamp NEVER matches an 8-digit filename -- querying only
        # the short form silently returns zero Supreme opinions, which is
        # exactly how the first 2020 sweep came back 885 ctapun / 0 supct.
        # OR together every filing day in [start, end]. Batching days into one
        # query cuts the number of navigations -- and each navigation is a roll
        # of the CAPTCHA dice that a human has to clear. Pagination is 10/page
        # regardless, so batching only saves the per-query initial load, but
        # that is still ~30% of the loads across a year.
        stamps = set()
        d = start
        while d <= end:
            if d.weekday() in FILING_WEEKDAYS:
                stamps.add(d.strftime("%m%d%y"))
                stamps.add(d.strftime("%m%d%Y"))
            d += datetime.timedelta(days=1)
        if not stamps:
            stamps = {start.strftime("%m%d%y"), start.strftime("%m%d%Y")}
        stamp_q = " OR ".join("url:%s" % s for s in sorted(stamps))
        if strategy == "filedate_bare":
            q = "(%s)" % stamp_q
        else:
            years = {start.year, end.year}
            dirs = " OR ".join(
                "url:/archive/%s/%d" % (c, y) for y in sorted(years)
                for c in ALL_CATS)
            q = "(%s) (%s)" % (dirs, stamp_q)
        return SEARCH + "?" + urlencode(dict(base, query=q,
                                             **{"start-date": "", "end-date": ""}))
    if strategy == "yearurl":
        # Ask by the year segment that appears in the PDF path itself.
        q = "(url:/archive/ctapun/%d OR url:/archive/ctappub/%d OR " \
            "url:/archive/supct/%d)" % (start.year, start.year, start.year)
        return SEARCH + "?" + urlencode(dict(base, query=q,
                                             **{"start-date": "", "end-date": ""}))
    raise ValueError("unknown strategy %r" % strategy)


STRATEGIES = ("filedate", "filedate_bare", "dateops", "daterange", "yearurl",
              "getform")


def looks_like_captcha(page):
    try:
        t = page.inner_text("body")[:800].lower()
    except Exception:
        return False
    return ("validate your request" in t or "solve this captcha" in t
            or "confirm you are a human" in t or "rdwr" in t)


def load(page, url, pace, human_wait_s=300):
    """Navigate once, gently, and wait for results. Never auto-solves."""
    page.wait_for_timeout(int(pace * 1000))
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except PWTimeout:
        pass
    try:
        page.wait_for_selector(ANY_ARCHIVE_SEL, timeout=20000, state="attached")
    except PWTimeout:
        pass
    if safe_count(page) == 0 and looks_like_captcha(page):
        print("\n  *** mn.gov bot-check. Solve it in the open Chrome window. ***")
        print("      (never solved automatically; waiting up to %ds)" % human_wait_s)
        waited = 0
        while waited < human_wait_s:
            page.wait_for_timeout(3000)
            waited += 3
            if safe_count(page) > 0:
                print("      cleared -- continuing.")
                return True
        print("      still blocked; giving up on this navigation.")
        return False
    return safe_count(page) > 0


def safe_count(page):
    """query_selector_all can race a navigation (the CAPTCHA page redirects),
    which raises 'Execution context was destroyed'. Treat that as zero."""
    try:
        return len(page.query_selector_all(ANY_ARCHIVE_SEL))
    except Exception:
        return 0


def hrefs_on_page(page):
    try:
        raw = page.eval_on_selector_all(
            ANY_ARCHIVE_SEL, "els => els.map(e => e.href)")
    except Exception:
        return []
    return [h for h in raw if CAT_RE.search(h)]


def window_is_honored(rows, start, end, slack_days=3):
    """Did the search ACTUALLY bound the window, or silently ignore it?

    Returns (ok, reason). Only rows whose filename carried a date count; a
    result set with no parseable dates cannot be verified and is rejected --
    silently hoovering the newest 100 opinions into a '2021' run is the exact
    failure this whole script is built to prevent.
    """
    dated = [d for _, d, _ in rows if d]
    if not dated:
        return False, "no filename-encoded dates to verify against"
    lo = (start - datetime.timedelta(days=slack_days)).isoformat()
    hi = (end + datetime.timedelta(days=slack_days)).isoformat()
    inside = [d for d in dated if lo <= d <= hi]
    frac = len(inside) / float(len(dated))
    if frac < 0.8:
        return False, ("only %d/%d dated results fall in %s..%s (e.g. %s)"
                       % (len(inside), len(dated), start, end,
                          ", ".join(sorted(set(dated))[:3])))
    return True, "%d/%d in window" % (len(inside), len(dated))


def collect_window(page, strategy, start, end, pace, max_pages):
    """Load a window and page through it. Returns (rows, note)."""
    url = build_url(strategy, start, end)
    if not load(page, url, pace):
        return [], "no results rendered"
    first = hrefs_on_page(page)
    rows = [parse_pdf_url(h) for h in first]
    ok, why = window_is_honored(rows, start, end)
    if not ok:
        return [], "WINDOW NOT HONORED: %s" % why
    # Record hrefs as we go: the download step needs the URL for every page's
    # rows, and only the LAST page's anchors are still in the DOM at the end.
    for h in first:
        c, _, _ = parse_pdf_url(h)
        if c:
            rows_url_map[c] = h

    seen = {r[0] for r in rows if r[0]}
    for pageno in range(2, max_pages + 1):
        href = None
        try:
            href = page.evaluate(
                """(n) => {
                    const a = Array.from(document.querySelectorAll('a'))
                      .find(e => (e.textContent||'').trim() === String(n)
                                 && /root-\\d+-\\d+/.test(e.href));
                    return a ? a.href : null;
                }""", pageno)
        except Exception:
            pass
        if not href:
            break
        if not load(page, href, pace):
            break
        page_hrefs = hrefs_on_page(page)
        more = [parse_pdf_url(h) for h in page_hrefs]
        fresh = [r for r in more if r[0] and r[0] not in seen]
        if not fresh:
            break
        for h in page_hrefs:
            c, _, _ = parse_pdf_url(h)
            if c:
                rows_url_map[c] = h
        for r in fresh:
            seen.add(r[0])
        rows.extend(fresh)
    else:
        # Fell off the end of the pager while still finding NEW opinions --
        # this window is truncated, not complete. Silent truncation is the one
        # way batching could quietly lose records, so say so loudly and let the
        # caller re-run the range with a smaller --batch-days.
        return rows, ("TRUNCATED: still finding new opinions at the page %d "
                      "ceiling; re-run this range with a smaller --batch-days"
                      % max_pages)
    return rows, "ok"


def download(page, rows, outdir, pace):
    """Fetch each PDF via in-page fetch. COA and Supreme go to separate dirs
    so each maps to one `ingest_pdfs --court` run."""
    got = skipped = failed = 0
    for case, iso, cat in rows:
        if not case:
            continue
        sub = "supreme" if cat in SUP_CATS else "appeals"
        d = os.path.join(outdir, sub)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "%s.pdf" % case)
        if os.path.exists(path):
            skipped += 1
            continue
        url = rows_url_map.get(case)
        if not url:
            failed += 1
            continue
        page.wait_for_timeout(int(pace * 250))
        try:
            res = page.evaluate(FETCH_JS, url)
        except Exception as e:
            print("    fetch error %s: %s" % (case, e))
            failed += 1
            continue
        if not res or not res.get("ok"):
            failed += 1
            continue
        with open(path, "wb") as f:
            f.write(base64.b64decode(res["b64"]))
        got += 1
    return got, skipped, failed


rows_url_map = {}


def iter_windows(args, since, until):
    """Yield (start, end) windows, newest first.

    For the filedate strategies a "window" is a SINGLE DAY, because the query
    keys on the `mmddyy` stamp in the filename. MN files appellate opinions on
    Mondays (verified across every sample we've seen), so walking Mondays costs
    ~52 queries a year instead of 365. `--weekdays` widens that when a holiday
    shifts a release -- sweep Mondays first, then re-run the thin weeks with
    `--weekdays 0,1,2` to catch a Tuesday/Wednesday release.
    """
    if args.strategy in ("filedate", "filedate_bare"):
        # Batch `--batch-days` calendar days into one query (their filing-day
        # stamps get OR'd together). Fewer queries = fewer CAPTCHA rolls for
        # the human. Keep batches modest: every extra filing day adds ~25
        # results, and >100 in one window hits the pager ceiling.
        step = max(1, args.batch_days)
        end = until
        while end >= since:
            start = max(since, end - datetime.timedelta(days=step - 1))
            if any((start + datetime.timedelta(days=i)).weekday() in FILING_WEEKDAYS
                   for i in range((end - start).days + 1)):
                yield start, end
            end = start - datetime.timedelta(days=1)
        return
    cur_end = until
    while cur_end >= since:
        cur_start = max(since, cur_end - datetime.timedelta(days=args.window_days - 1))
        yield cur_start, cur_end
        cur_end = cur_start - datetime.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="Try each strategy once against a known window and "
                         "report which actually bounds dates. Do this first.")
    ap.add_argument("--strategy", choices=STRATEGIES,
                    help="Query strategy verified by --probe.")
    ap.add_argument("--since", help="ISO date, inclusive lower bound.")
    ap.add_argument("--until", help="ISO date, inclusive upper bound.")
    ap.add_argument("--window-days", type=int, default=14,
                    help="Backward step for non-filedate strategies (default 14). "
                         "Ignored by filedate, which steps one day at a time.")
    ap.add_argument("--batch-days", type=int, default=7,
                    help="filedate only: calendar days per query (default 7 = "
                         "one Mon+Wed pair). Each query is a CAPTCHA roll for "
                         "the human, so batching cuts interruptions; but each "
                         "filing day adds ~25 results and >100 in a window "
                         "hits the pager ceiling, which is reported as "
                         "TRUNCATED rather than silently accepted.")
    ap.add_argument("--weekdays", default="0,2",
                    help="filedate only: weekdays to query, Mon=0 (default "
                         "'0,2'). THE COURTS USE DIFFERENT DAYS: the Court of "
                         "Appeals files Mondays, the Supreme Court files "
                         "WEDNESDAYS. A Monday-only sweep silently returns "
                         "zero Supreme opinions -- that is how the first 2020 "
                         "pass came back 885 ctapun / 0 supct. Tue/Thu/Fri were "
                         "probed and are empty.")
    ap.add_argument("--max-pages", type=int, default=10,
                    help="Pager depth per window (site exposes ~10).")
    ap.add_argument("--pace", type=float, default=8.0,
                    help="Seconds between navigations. The wall CAPTCHAs on "
                         "rapid loads -- do not lower this casually.")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(),
                                                  "mn_backfill_pdf"))
    ap.add_argument("--no-download", action="store_true",
                    help="List only; don't fetch PDFs. Pair with --manifest.")
    ap.add_argument("--manifest",
                    help="Append '<case> <category> <iso_date> <url>' per "
                         "opinion. PDFs are NOT bot-walled, so the fast shape "
                         "is: browser collects URLs (--no-download --manifest), "
                         "then NFSN curls them in bulk. Browser time drops from "
                         "~2min to ~25s per Monday.")
    args = ap.parse_args()

    if not args.probe and not (args.strategy and args.since and args.until):
        ap.error("either --probe, or --strategy with --since and --until")

    global FILING_WEEKDAYS
    FILING_WEEKDAYS = {int(x) for x in args.weekdays.split(",") if x.strip()}

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=False,
            viewport={"width": 1300, "height": 1100})
        page = ctx.new_page()
        for stale in list(ctx.pages):
            if stale is not page:
                stale.close()

        if args.probe:
            # Default: a Monday we KNOW has real opinions (OPa210414-112221.pdf
            # was verified live). --since/--until override it.
            start = (datetime.date.fromisoformat(args.since) if args.since
                     else datetime.date(2021, 11, 22))
            end = (datetime.date.fromisoformat(args.until) if args.until
                   else start)
            only = [args.strategy] if args.strategy else list(STRATEGIES)
            print("Probing strategies against %s..%s" % (start, end))
            print("(one navigation each, %.0fs apart)\n" % args.pace)
            for s in only:
                print("=== %s" % s)
                if not load(page, build_url(s, start, end), args.pace):
                    print("    no results rendered\n")
                    continue
                rows = [parse_pdf_url(h) for h in hrefs_on_page(page)]
                ok, why = window_is_honored(rows, start, end)
                dates = sorted({d for _, d, _ in rows if d})
                print("    %d results; dates %s" % (len(rows), dates[:4]))
                print("    %s -- %s\n" % ("HONORS WINDOW" if ok else "IGNORES WINDOW", why))
            ctx.close()
            return

        since = datetime.date.fromisoformat(args.since)
        until = datetime.date.fromisoformat(args.until)
        total = failed_windows = 0
        skipped_windows = []

        for cur_start, cur_end in iter_windows(args, since, until):
            label = (str(cur_start) if cur_start == cur_end
                     else "%s .. %s" % (cur_start, cur_end))
            print("\n--- %s" % label)
            rows, note = collect_window(page, args.strategy, cur_start, cur_end,
                                        args.pace, args.max_pages)
            if note != "ok":
                print("    SKIPPED: %s" % note)
                failed_windows += 1
                skipped_windows.append((label, note))
                continue
            print("    %d opinions" % len(rows))
            if args.manifest:
                with open(args.manifest, "a", encoding="utf8") as mf:
                    for case, iso, cat in rows:
                        url = rows_url_map.get(case)
                        if case and url:
                            mf.write("%s\t%s\t%s\t%s\n"
                                     % (case, cat, iso or "", url))
            if not args.no_download:
                got, had, bad = download(page, rows, args.out, args.pace)
                print("    downloaded %d, already had %d, failed %d"
                      % (got, had, bad))
            total += len(rows)

        print("\n%d opinions across the sweep; %d windows skipped."
              % (total, failed_windows))
        for label, note in skipped_windows:
            print("  SKIPPED %s -- %s" % (label, note))
        if failed_windows:
            print("Skipped windows are NOT a partial success -- re-run them. A "
                  "skipped window means the date filter stopped being honored "
                  "(or the page never rendered), NOT that the window was empty.")
        print("PDFs in %s (appeals/ and supreme/ subdirs)." % args.out)
        ctx.close()


if __name__ == "__main__":
    main()
