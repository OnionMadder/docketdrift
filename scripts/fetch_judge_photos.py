"""Download every judge's external portrait into the repo's static tree and
emit a localization manifest, so the site SELF-HOSTS all judge photos instead
of hotlinking mncourts.gov / azcourts.gov / courts.nh.gov (which break if
those sites go down -- and NH's is Akamai-blocked anyway).

Run locally (residential IP). MN/AZ portraits download directly; NH portraits
come from the Playwright scrape (scripts/nh_scraper / Temp/nh_judges_out).

Produces (both committed to the repo):
  opinions/static/opinions/judges/<state>/<slug>.<ext>
  opinions/data/judge_localization.json   (manifest)

Then on the server: `manage.py localize_judge_photos` repoints each judge's
photo_url to the self-hosted /static/ URL and applies the scraped NH bios.
"""
import json, os, shutil, tempfile, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_BASE = os.path.join(REPO, "opinions", "static", "opinions", "judges")
DATA_DIR = os.path.join(REPO, "opinions", "data")
_TMP = tempfile.gettempdir()
# judge_photo_data.json: dump of existing judges' (state, slug, name, photo_url)
# + NH seated roster, produced by querying the server before running this.
JUDGE_DATA = os.path.join(_TMP, "judge_photo_data.json")
# nh_justices.json + portraits: output of scripts/nh_scraper/scrape_nh_justices.py
NH_SCRAPE = os.path.join(_TMP, "nh_judges_out", "nh_justices.json")
NH_IMG_DIR = os.path.join(_TMP, "nh_judges_out")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def ext_for(b: bytes) -> str:
    if b[:4] == b"\x89PNG":
        return ".png"
    if b[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ".jpg"


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read()


def last_name(name: str) -> str:
    toks = name.replace(",", " ").split()
    return toks[-1].lower() if toks else ""


os.makedirs(DATA_DIR, exist_ok=True)
jd = json.load(open(JUDGE_DATA, encoding="utf-8"))
manifest, fails = [], []

# --- MN + AZ: download external portrait -> repo static ---
for j in jd["with_photo"]:
    if j["state"] == "NH":
        continue  # NH handled from the scrape below (source is Akamai-blocked)
    st = j["state"].lower()
    try:
        data = download(j["photo_url"])
        if len(data) < 500:
            raise ValueError("suspiciously small (%d bytes)" % len(data))
        ext = ext_for(data)
        d = os.path.join(STATIC_BASE, st)
        os.makedirs(d, exist_ok=True)
        fn = j["slug"] + ext
        with open(os.path.join(d, fn), "wb") as fh:
            fh.write(data)
        manifest.append({"state": j["state"], "slug": j["slug"],
                         "photo": "opinions/judges/%s/%s" % (st, fn)})
        print("OK   %s/%s  %dB" % (st, fn, len(data)))
    except Exception as e:
        fails.append((j["state"], j["slug"], repr(e)[:120]))
        print("FAIL %s %s -- %r" % (j["state"], j["slug"], e))

# --- NH: match scraped 5 -> seated rows by last name; copy portrait + bio ---
nh_scrape = json.load(open(NH_SCRAPE, encoding="utf-8"))
seated = {last_name(s["name"]): s["slug"] for s in jd["nh_seated"]}
for s in nh_scrape:
    ln = last_name(s["full_name"])
    slug = seated.get(ln)
    if not slug:
        fails.append(("NH", s["full_name"], "no seated match for last name %r" % ln))
        print("FAIL NH no seated match: %s" % s["full_name"]); continue
    entry = {"state": "NH", "slug": slug,
             "bio_summary": s.get("bio_summary", ""),
             "appointment_date": s.get("appointment_date", ""),
             "bio_url": s.get("bio_url", ""),
             "role": s.get("role", "")}
    src = os.path.join(NH_IMG_DIR, s.get("photo_file", "") or "")
    if s.get("photo_file") and os.path.exists(src):
        d = os.path.join(STATIC_BASE, "nh"); os.makedirs(d, exist_ok=True)
        fn = slug + ".jpg"
        shutil.copyfile(src, os.path.join(d, fn))
        entry["photo"] = "opinions/judges/nh/%s" % fn
        print("OK   nh/%s  (matched %s)" % (fn, s["full_name"]))
    else:
        print("WARN NH no photo file for %s" % s["full_name"])
    manifest.append(entry)

with open(os.path.join(DATA_DIR, "judge_localization.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2, ensure_ascii=False)

print("\nmanifest entries: %d   photos saved under: %s" % (len(manifest), STATIC_BASE))
print("fails: %d" % len(fails))
for f in fails:
    print("  FAIL", f)
