"""Scrape the current NH Supreme Court justices (name, role, appointment date,
bio, and portrait) from courts.nh.gov.

courts.nh.gov is behind Akamai and 403s every non-browser client -- curl and
server-side fetches both fail, even from a residential IP. The workaround
(CLAUDE.md task #41) is a REAL browser on a residential machine. This uses
Playwright driving the installed Google Chrome (``channel="chrome"``, headed),
which Akamai lets through. Portraits are fetched with an in-page same-origin
``fetch`` (Playwright's request API is fingerprinted and 403s).

Run on Onion's Windows box (residential IP):

    pip install playwright          # one-time
    python scripts/nh_scraper/scrape_nh_justices.py

Output (under your temp dir, e.g. %TEMP%\\nh_judges_out):
    nh_justices.json    + one <slug>.jpg portrait per justice

Then feed it into the site:
    python scripts/fetch_judge_photos.py     # copies portraits into static/
                                             # + (re)builds the manifest
    git add opinions/static/opinions/judges opinions/data/judge_localization.json
    git commit && git push                   # deploy
    ssh ... manage.py collectstatic && restart && manage.py localize_judge_photos

Requires: playwright + a Google Chrome install. NOT run on NFSN (residential
browser only).
"""
import base64, json, os, re, tempfile
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROSTER = "https://www.courts.nh.gov/our-courts/supreme-court/about/justices"
OUT = os.path.join(tempfile.gettempdir(), "nh_judges_out")
os.makedirs(OUT, exist_ok=True)

# Same-origin fetch inside the page: carries Akamai cookies + real-browser
# fingerprint, so the image bytes come back (a plain request 403s).
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


def parse_appointment(text: str) -> str:
    for pat in (r"sworn in\b.*?\bon\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
                r"joined the bench on\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})"):
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return ""


def clean_bio(raw: str) -> str:
    # Drop the nav/breadcrumb lead-in: keep text after the "About Supreme
    # Court <Role> <Full Name>." line. GREEDY up to the last period on that
    # line so middle-initial periods ("Gordon J.") don't cut it short.
    parts = re.split(r"About Supreme Court [^\n]+\.\s*", raw, maxsplit=1)
    body = parts[-1] if len(parts) > 1 else raw
    for marker in ("Speeches and statements", "Swearing-in Ceremony",
                   "These documents were created", "Statement by the Honorable"):
        i = body.find(marker)
        if i != -1:
            body = body[:i]
    body = body.replace("​", "")          # zero-width spaces from source
    body = re.sub(r"[ \t]+\n", "\n", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=False)
    page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    page.goto(ROSTER, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)

    bio_links = page.eval_on_selector_all(
        "a",
        "els => els.map(e=>[e.textContent.trim().replace(/\\s+/g,' '), e.href])"
        ".filter(x=>/\\/about\\/justices\\/[a-z]/.test(x[1]))",
    )
    seen, links = set(), []
    for txt, href in bio_links:
        if href not in seen:
            seen.add(href); links.append((txt, href))

    imgs = page.eval_on_selector_all(
        "img",
        "els => els.map(e=>[(e.getAttribute('alt')||'').trim(), e.currentSrc||e.src])"
        ".filter(x=>/justice/i.test(x[0]) && /\\.(jpg|jpeg|png)/i.test(x[1]))",
    )

    def photo_for(name):
        ln = name.split()[-1].lower()
        for alt, src in imgs:
            if ln in alt.lower():
                return src
        return ""

    out = []
    for txt, href in links:
        role = "CHIEF_JUSTICE" if "chief justice" in txt.lower() else "ASSOCIATE_JUSTICE"
        name = re.sub(r"(?i)\b(about|chief|senior|associate|justice)\b", "", txt).strip(" ,")
        name = re.sub(r"\s+", " ", name).strip()
        out.append({"full_name": name, "role": role, "bio_url": href,
                    "photo_styled": photo_for(name)})

    for j in out:  # fetch portraits while on the same-origin roster page
        url = j.get("photo_styled")
        if not url:
            continue
        res = page.evaluate(FETCH_JS, url)
        if res.get("ok"):
            ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
            fn = slugify(j["full_name"]) + ext
            with open(os.path.join(OUT, fn), "wb") as fh:
                fh.write(base64.b64decode(res["b64"]))
            j["photo_file"] = fn
        else:
            j["photo_fetch"] = res

    for j in out:  # full bio text + appointment date from each bio page
        page.goto(j["bio_url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1000)
        raw = ""
        for sel in ("main", "#content", "article", ".region-content"):
            el = page.query_selector(sel)
            if el:
                raw = el.inner_text(); break
        j["appointment_date"] = parse_appointment(raw)
        j["bio_summary"] = clean_bio(raw)
        j.pop("photo_styled", None)

    with open(os.path.join(OUT, "nh_justices.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    b.close()

print("scraped %d justices -> %s" % (len(out), OUT))
for j in out:
    print("  - %-22s | %-17s | appt=%-18s | photo=%-26s | bio=%d"
          % (j["full_name"], j["role"], j["appointment_date"] or "?",
             j.get("photo_file") or ("FAIL %s" % j.get("photo_fetch")),
             len(j["bio_summary"])))
