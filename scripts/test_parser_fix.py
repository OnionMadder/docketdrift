"""Confidence check: does the backslash-escape dialect actually fix opinions parsing?

Reads the first 1,000 rows of the raw opinions bz2 with the new dialect and
reports how many have a populated cluster_id vs empty. Pre-fix this was ~0
populated, ~1000 empty. Post-fix should be ~1000 populated, ~0 empty.
"""
import bz2
import csv
from pathlib import Path

PATH = Path(r"C:\Users\kelly\courtlistener-bulk\opinions-2026-03-31.csv.bz2")

csv.field_size_limit(50_000_000)

with bz2.open(PATH, "rt", encoding="utf-8", errors="replace", newline="") as fh:
    reader = csv.DictReader(
        fh,
        quotechar='"',
        escapechar="\\",
        doublequote=False,
    )

    populated_cluster = 0
    empty_cluster = 0
    populated_id = 0
    populated_text = 0
    sample = []

    for i, row in enumerate(reader):
        if i >= 1000:
            break
        if row.get("cluster_id"):
            populated_cluster += 1
        else:
            empty_cluster += 1
        if row.get("id"):
            populated_id += 1
        if row.get("plain_text"):
            populated_text += 1
        if i < 3:
            sample.append({
                "id": row.get("id", ""),
                "cluster_id": row.get("cluster_id", ""),
                "type": row.get("type", ""),
                "plain_text_len": len(row.get("plain_text") or ""),
            })

print(f"First 1000 rows of opinions-2026-03-31.csv.bz2:")
print(f"  populated id:          {populated_id}")
print(f"  populated cluster_id:  {populated_cluster}")
print(f"  empty cluster_id:      {empty_cluster}")
print(f"  with plain_text:       {populated_text}")
print()
print("first 3 rows:")
for s in sample:
    print(f"  {s}")
