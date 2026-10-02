"""Delete leftover loose PDFs (+ their derivatives) from the MN IA item.

Keeps: the zip, manifest.csv, README.md, IA's own item files, history/.
Usage: python ia_cleanup.py [--apply]
"""
import sys, time
import internetarchive as ia

ID = "minnesota-appellate-opinions-2017-2026"
KEEP = {"mn-appellate-opinions-2017-2026.zip", "manifest.csv", "README.md"}
APPLY = "--apply" in sys.argv

item = ia.get_item(ID)
files = item.files
originals = {f["name"] for f in files if f.get("source") == "original"}

def protected(name):
    return (name in KEEP or name.startswith("history/")
            or name.startswith(ID + "_") or name.startswith("__ia_thumb"))

pdfs = sorted(n for n in originals if n.lower().endswith(".pdf") and not protected(n))
# Pass 2 (--sweep): after the PDFs are gone, any non-protected derivative left
# is an orphan. Derivatives chain through intermediates, so this must re-read.
SWEEP = "--sweep" in sys.argv
orphans = sorted(f["name"] for f in files
                 if f.get("source") == "derivative" and not protected(f["name"])
                 and f["name"] != "mn-appellate-opinions-2017-2026.zip") if SWEEP else []
if SWEEP: pdfs = []
print(f"loose PDFs: {len(pdfs)}  orphan derivatives: {len(orphans)}", flush=True)
assert not any(n in KEEP for n in pdfs + orphans)
if not APPLY:
    print("dry run; sample:", pdfs[:3], orphans[:3]); sys.exit(0)

SESSION = ia.get_session()
MAX_PENDING = 60  # past attempts died on bucket_tasks_queued; stay well under it


def wait_for_queue():
    """Block until IA's task queue for this item drains below MAX_PENDING."""
    while True:
        try:
            s = SESSION.get_tasks_summary(ID)
            pending = s.get("queued", 0) + s.get("running", 0)
        except Exception as e:  # noqa: BLE001
            print(f"  summary failed ({str(e)[:80]}); waiting", flush=True)
            pending = MAX_PENDING
        if pending < MAX_PENDING:
            return
        print(f"  queue {pending} >= {MAX_PENDING}; sleeping 120s", flush=True)
        time.sleep(120)


def delete(name, cascade):
    wait_for_queue()
    for attempt in range(8):
        try:
            r = item.get_file(name).delete(cascade_delete=cascade, retries=2)
            if r is None or getattr(r, "status_code", 200) < 300:
                return True
            code = r.status_code
        except Exception as e:  # noqa: BLE001
            code = str(e)[:120]
        print(f"  retry {name} ({code})", flush=True)
        if "bucket_tasks_queued" in str(code) or str(code) in ("503", "429"):
            time.sleep(120)
            wait_for_queue()  # throttle refusal: wait it out, don't burn attempts
            continue
        time.sleep(min(600, 30 * 2 ** attempt))
    return False

targets = pdfs + orphans
for a in sys.argv:
    if a.startswith("--limit="):
        targets = targets[:int(a.split("=")[1])]
ok = fail = 0
for i, name in enumerate(targets, 1):
    if delete(name, cascade=name in pdfs):
        ok += 1
    else:
        fail += 1
        print(f"  FAILED {name}", flush=True)
    if i % 25 == 0:
        print(f"{i}/{len(targets)} ok={ok} fail={fail}", flush=True)
print(f"DONE ok={ok} fail={fail}", flush=True)
