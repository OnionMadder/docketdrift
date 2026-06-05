"""Confirm CL bulk courts file parses and locate the MN court IDs.

DuckDB's CSV sniffer choked on this file (likely a row with an unbalanced
embedded quote somewhere early), so we use Python's stdlib ``csv`` module
which handles the standard CSV-quoting dialect that CL emits without drama.
The courts file is 79 KB compressed -- speed isn't the point here, we just
want to verify the format and pin down the exact MN court IDs before we
write the real filter pass.
"""
import bz2
import csv
import sys
from pathlib import Path

PATH = Path(r"C:\Users\kelly\courtlistener-bulk\courts-2026-03-31.csv.bz2")

if not PATH.exists():
    sys.exit(f"Not found: {PATH}")

# csv module needs the field-size cap raised -- CL has long quoted notes.
csv.field_size_limit(50_000_000)

with bz2.open(PATH, "rt", encoding="utf-8", newline="") as fh:
    reader = csv.DictReader(fh)
    print(f"columns ({len(reader.fieldnames or [])}): {reader.fieldnames}\n")

    mn_rows = []
    total = 0
    for row in reader:
        total += 1
        rid = row.get("id") or ""
        full = (row.get("full_name") or "").lower()
        if "minnesota" in full or rid.startswith("minn"):
            mn_rows.append(row)

print(f"{total} total courts in file; {len(mn_rows)} Minnesota-related:\n")
for r in mn_rows:
    rid = r.get("id", "")
    jur = r.get("jurisdiction", "") or "-"
    short = r.get("short_name", "")
    full = r.get("full_name", "")
    print(f"  id={rid:<14} jur={jur:<4} short={short!r:<32} full={full!r}")

print()
print("DocketDrift only ingests state appellate -- expect 'minn' + 'minnctapp' in the list.")
