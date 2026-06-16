"""Download recent NH Supreme Court slip-opinion PDFs from courts.nh.gov.

Same Akamai workaround as the judges scraper: headed real-Chrome (Playwright,
``channel="chrome"``), and the PDF bytes are pulled with an in-page same-origin
``fetch`` (a plain request 403s even from a residential IP).

Opinions are listed by year at
``/our-courts/supreme-court/orders-and-opinions/opinions/<year>`` -- each entry
has a citation ("2026 N.H. 24, State v. Montgomery"), a "Date: MM/DD/YYYY", and
a PDF link. We download the OPINION PDFs only (skipping the dictionary
"Related Documents" Merriam-Webster/OED links) issued on/after ``--since``.

Usage (on Onion's residential Windows box):
    python scripts/nh_scraper/scrape_nh_opinions.py --since 2026-06-01

Then ship + ingest (ingest_pdfs dedups on (court, case_number), so re-grabbing
the boundary opinion is harmless):
    scp <tempdir>/nh_opinions_pdf/*.pdf docketdrift:/tmp/nhpdf/
    ssh ... manage.py ingest_pdfs --dir /tmp/nhpdf --state NH --court supreme
"""
import argparse, base64, datetime, os, re, tempfile
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

BASE = "https://www.courts.nh.gov/our-courts/supreme-court/orders-and-opinions/opinions/%d"

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=datetime.date.today().year)
    ap.add_argument("--since", default="",
                    help="ISO date (YYYY-MM-DD); only download opinions issued on/after this.")
    args = ap.parse_args()

    since = datetime.date.fromisoformat(args.since) if args.since else None
    out = os.path.join(tempfile.gettempdir(), "nh_opinions_pdf")
    os.makedirs(out, exist_ok=True)
    yr = str(args.year)

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=False)
        page = b.new_context(viewport={"width": 1280, "height": 1100}).new_page()
        page.goto(BASE % args.year, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        main_el = page.query_selector("main") or page.query_selector("body")
        body = main_el.inner_text() if main_el else ""
        # citation number -> issue date (non-greedy from each citation to its Date)
        num_date = {}
        for n, d in re.findall(yr + r" N\.H\. (\d+),.*?Date:\s*(\d{2}/\d{2}/\d{4})", body, re.DOTALL):
            mm, dd, yyyy = d.split("/")
            num_date[int(n)] = datetime.date(int(yyyy), int(mm), int(dd))

        # citation number -> opinion PDF url (text starts with the citation;
        # this excludes the dictionary "Related Documents" PDFs)
        links = page.eval_on_selector_all(
            "a", "els=>els.map(e=>[e.textContent.trim().replace(/\\s+/g,' '), e.href])")
        num_url = {}
        for t, h in links:
            m = re.match(yr + r" N\.H\. (\d+),", t)
            if m and ".pdf" in h.lower():
                num_url[int(m.group(1))] = h

        targets = []
        for num in sorted(num_url):
            dt = num_date.get(num)
            if dt is None or (since and dt < since):
                continue
            targets.append((num, dt, num_url[num]))
        print("targets:", [(n, dt.isoformat()) for n, dt, _ in targets])

        got = 0
        for num, dt, url in targets:
            res = page.evaluate(FETCH_JS, url)
            if res.get("ok"):
                data = base64.b64decode(res["b64"])
                fn = os.path.basename(urlparse(url).path)
                with open(os.path.join(out, fn), "wb") as fh:
                    fh.write(data)
                got += 1
                print("OK   %s N.H. %d  %s  -> %s  (%d B)" % (yr, num, dt.isoformat(), fn, len(data)))
            else:
                print("FAIL %s N.H. %d  %s" % (yr, num, res))
        b.close()

    print("\ndownloaded %d opinion PDF(s) -> %s" % (got, out))


if __name__ == "__main__":
    main()
