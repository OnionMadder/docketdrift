"""Download recent MN Court of Appeals opinion PDFs from mn.gov's archive.

Mirrors the NH model (scripts/nh_scraper/): a HEADED real-Chrome Playwright
session on Onion's residential Windows box lists the opinions, downloads the
PDFs with an in-page same-origin fetch, and hands them to
`ingest_pdfs --state MN --court appeals` (which dedups on (court, case_number)).

WHY A BROWSER (and why it's delicate)
-------------------------------------
mn.gov's law-library search sits behind Radware Bot Manager. A single fresh
real-Chrome load passes cleanly, but rapid automated reloads trip a CAPTCHA
(observed 2026-07-20). Two consequences shape this script:

  1. It uses a PERSISTENT browser profile (user-data-dir). Once the site has
     cleared this profile, the clearance cookie persists, so later runs look
     like the same returning human rather than a fresh bot each time.
  2. It NEVER tries to solve a CAPTCHA (we don't, ever). If one appears, it
     prints a clear notice and WAITS -- the headed window is visible and a
     human is logged on (same "run only when logged on" model as NH) -- for
     the person to solve it once, then continues. The persistent profile banks
     that clearance for next time.

And the load itself is JS-injected + load-timing-variable, so we wait for the
RESULT ANCHORS, never for networkidle (a chat widget keeps the network live
forever) and never a fixed sleep.

THE ARCHIVE (recon 2026-07-20)
------------------------------
`opinions-archive.jsp` lands on a reverse-chronological list of ALL recent
opinions -- COA precedential + COA nonprecedential + Supreme, all pre-selected
-- 10 per page, newest first, ~1400 deep. Each row carries a case title, a
"Date: <Month D, YYYY>", and a PDF link whose path encodes the category:

    .../law-library-stat/archive/ctappub/2026/OPa251985-071326.pdf   COA published
    .../law-library-stat/archive/ctapun/2026/OPa251570-071326.pdf    COA unpublished
    .../law-library-stat/archive/COAspectorders/...                  COA orders
    .../law-library-stat/archive/supct/2026/OPA231400-07152026.pdf   Supreme (skipped)

So we don't submit a search at all -- we page the default list newest-first,
keep the COA PDFs whose row date is >= --since, and stop once we page past it.
Case number / date / disposition are re-derived from each PDF by the MN parser
at ingest, so the scraper only needs the URL and the row date (to bound the
walk).

Usage (on the residential Windows box, logged on):
    python scripts/mn_scraper/scrape_mn_coa.py --since 2026-06-01

Then ship + ingest (ingest_pdfs dedups, so re-grabbing the boundary is safe):
    scp <tempdir>/mn_coa_pdf/*.pdf docketdrift:/tmp/mnpdf/
    ssh ... manage.py ingest_pdfs --dir /tmp/mnpdf --state MN --court appeals
"""
import argparse
import base64
import datetime
import os
import re
import sys
import tempfile
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ARCHIVE = "https://mn.gov/law-library/search/opinions-archive.jsp"

# Persistent profile so a cleared bot-challenge is remembered across runs.
PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
    "docketdrift_mn_scraper_profile",
)

# COA categories only. Suprece ("supct") is intentionally excluded -- this
# pipeline is COA-scoped (that's where the coverage gap lives). Add "supct"
# here if MN Supreme is ever brought under the same scraper.
COA_CATEGORIES = ("ctappub", "ctapun", "coaspectorders")
_CAT_ALT = "|".join(COA_CATEGORIES)
COA_HREF_RE = re.compile(
    r"law-library-stat/archive/(?:%s)/" % _CAT_ALT, re.IGNORECASE
)
ANY_ARCHIVE_SEL = "a[href*='law-library-stat/archive/']"

# In-page same-origin fetch -- the NH lesson: Playwright's request API is
# fingerprinted and blocked, but a fetch() from the passing page context works.
FETCH_JS = """
async (url) => {
  try {
    const r = await fetch(url, {credentials: 'include'});
    if (!r.ok) return {ok:false, status:r.status};
    const bytes = new Uint8Array(await r.arrayBuffer());
    let bin = ''; const chunk = 8192;
    for (let i=0;i<bytes.length;i+=chunk){ bin += String.fromCharCode.apply(null, bytes.subarray(i,i+chunk)); }
    return {ok:true, status:r.status, b64: btoa(bin)};
  } catch (e) { return {ok:false, err: String(e)}; }
}
"""

# Pull (pdf_href, iso_date) for every COA row on the current page. Dates come
# from the row's "Date: Month D, YYYY" label; the anchor and that label share a
# result container, so we climb from each anchor to the nearest block holding a
# Date and read it there.
ROWS_JS = r"""
() => {
  const MONTHS = {january:1,february:2,march:3,april:4,may:5,june:6,july:7,
                  august:8,september:9,october:10,november:11,december:12};
  const cats = /law-library-stat\/archive\/(ctappub|ctapun|coaspectorders)\//i;
  const out = [];
  for (const a of Array.from(document.querySelectorAll('a'))) {
    if (!cats.test(a.href)) continue;
    let el = a, iso = null;
    for (let i = 0; i < 6 && el; i++, el = el.parentElement) {
      const m = (el.textContent || '').match(/Date:\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})/);
      if (m) {
        const mo = MONTHS[m[1].toLowerCase()];
        if (mo) iso = m[3] + '-' + String(mo).padStart(2,'0') + '-' + String(m[2]).padStart(2,'0');
        break;
      }
    }
    out.push({href: a.href, date: iso});
  }
  return out;
}
"""


def looks_like_captcha(page):
    txt = ""
    try:
        txt = page.inner_text("body")[:800].lower()
    except Exception:
        pass
    return ("validate your request" in txt or "solve this captcha" in txt
            or "confirm you are a human" in txt)


def ensure_results(page, url, human_wait_s=240):
    """Load ``url`` and return once COA/archive result anchors are present.

    Gentle by design (rapid reloads are what trip the bot wall):
      - one navigation, then poll up to ~20s for the JS-injected results;
      - if a CAPTCHA is showing, DON'T reload and DON'T solve it -- wait for
        the logged-on human to clear it in the visible window, polling for the
        results to appear (up to ``human_wait_s``);
      - only if the page is merely slow/empty (no captcha) do we reload once.
    """
    for attempt in range(1, 4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except PWTimeout:
            pass
        try:
            page.wait_for_selector(ANY_ARCHIVE_SEL, timeout=20000, state="attached")
            if page.query_selector_all(ANY_ARCHIVE_SEL):
                return True
        except PWTimeout:
            pass

        if looks_like_captcha(page):
            print("\n*** mn.gov is showing a bot-check CAPTCHA. ***")
            print("    Solve it in the Chrome window that's open (the results")
            print("    will load once you do). Waiting up to %d s..." % human_wait_s)
            waited = 0
            while waited < human_wait_s:
                page.wait_for_timeout(3000)
                waited += 3
                if page.query_selector_all(ANY_ARCHIVE_SEL):
                    print("    cleared -- results loaded, continuing.")
                    return True
            print("    still blocked after waiting; giving up.")
            return False

        print("  results not present (attempt %d); reloading once" % attempt)
        page.wait_for_timeout(2500)
    return False


def go_to_next_page(page, next_num):
    """Advance to page ``next_num``; return True if its results rendered.

    The pager links are real navigable URLs -- ``opinions-archive.jsp?url=
    <vivisimo query>...root-<offset>-10`` where offset = (page-1)*10 -- so we
    read the target page's href off the current pager and hand it to
    ensure_results(). That reuses the robust load-and-reload-on-empty path,
    which matters: the paginated view is as load-timing-variable as the first
    page (a single click-then-wait renders 0 rows, which is the bug this
    replaces). The vivisimo session token in the href is only valid within
    this browser session, so we must NOT construct these URLs ourselves --
    always take the href the page hands us.

    The default pager window shows pages 1-10 (100 opinions). Going past it
    needs the window to shift (a >10 target isn't a visible link); we log that
    boundary and stop rather than silently truncating -- deep backfill should
    narrow --since instead.
    """
    href = page.evaluate(
        """(n) => {
            const a = Array.from(document.querySelectorAll('a'))
              .find(e => (e.textContent||'').trim() === String(n)
                         && /root-\\d+-\\d+/.test(e.href));
            return a ? a.href : null;
        }""",
        next_num,
    )
    if not href:
        print("  page %d is beyond the visible pager window; stopping "
              "(narrow --since for deeper coverage)." % next_num)
        return False
    return ensure_results(page, href)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="",
                    help="ISO date (YYYY-MM-DD); download COA opinions dated on/after this.")
    ap.add_argument("--max-pages", type=int, default=60,
                    help="Safety cap on pages walked (10 opinions/page).")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "mn_coa_pdf"),
                    help="Directory to write PDFs into.")
    args = ap.parse_args()

    since = datetime.date.fromisoformat(args.since) if args.since else None
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)

    seen_urls = set()
    targets = []          # (href, iso_date)
    stop = False

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=False,
            viewport={"width": 1300, "height": 1100},
        )
        # A persistent context RESTORES the previous session's tabs, so a prior
        # run left on a deep pager URL (root-10-10) would otherwise reopen
        # mid-list -- and the walk would start there and miss the newest page.
        # Start from one clean page and let ensure_results navigate to the base
        # archive (page 1, newest-first) explicitly.
        page = ctx.new_page()
        for stale in list(ctx.pages):
            if stale is not page:
                stale.close()

        if not ensure_results(page, ARCHIVE):
            print("Could not load the opinions archive. Aborting.")
            ctx.close()
            sys.exit(1)

        page_num = 1
        while page_num <= args.max_pages and not stop:
            rows = page.evaluate(ROWS_JS)
            page_new = 0
            for row in rows:
                href, d = row["href"], row["date"]
                if not COA_HREF_RE.search(href):
                    continue
                # Stop once we've clearly paged past the window. Undated rows
                # never trigger the stop (we can't place them) but are still
                # collected, so nothing is silently dropped.
                if since and d:
                    if datetime.date.fromisoformat(d) < since:
                        stop = True
                        continue
                if href not in seen_urls:
                    seen_urls.add(href)
                    targets.append((href, d))
                    page_new += 1
            print("page %d: %d COA rows, %d new in-range (total %d)"
                  % (page_num, len(rows), page_new, len(targets)))
            if stop:
                break
            if not go_to_next_page(page, page_num + 1):
                print("no further pages.")
                break
            page_num += 1
            page.wait_for_timeout(1500)   # human-ish pacing between pages

        print("\ncollected %d COA PDF URL(s) in range; downloading..." % len(targets))
        got = 0
        for href, d in targets:
            res = page.evaluate(FETCH_JS, href)
            if res.get("ok"):
                data = base64.b64decode(res["b64"])
                fn = os.path.basename(urlparse(href).path)
                with open(os.path.join(args.out, fn), "wb") as fh:
                    fh.write(data)
                got += 1
                print("OK   %s  %s  (%d B)" % (d or "?", fn, len(data)))
            else:
                print("FAIL %s  %s  -> %s" % (d or "?", href, res))
            page.wait_for_timeout(600)   # pace downloads; don't hammer
        ctx.close()

    print("\ndownloaded %d MN COA PDF(s) -> %s" % (got, args.out))
    if got == 0:
        sys.exit(0 if not targets else 1)


if __name__ == "__main__":
    main()
