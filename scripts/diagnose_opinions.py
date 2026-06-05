"""Quick health check on mn-subset/opinions.csv.

Tells us whether the 316 vs 47,941 mismatch is a parser corruption issue
(most rows have empty id) or just a script bug in id collection (rows are
fine, summary count is wrong).
"""
import csv
from pathlib import Path

PATH = Path(r"C:\Users\kelly\courtlistener-bulk\mn-subset\opinions.csv")

csv.field_size_limit(50_000_000)

total = 0
empty_id = 0
empty_cluster = 0
unique_ids: set[str] = set()
unique_cluster_ids: set[str] = set()
sample = []

with open(PATH, "r", encoding="utf-8", newline="") as fh:
    reader = csv.DictReader(fh)
    print(f"columns: {reader.fieldnames}\n")
    for row in reader:
        total += 1
        rid = row.get("id") or ""
        cid = row.get("cluster_id") or ""
        if rid:
            unique_ids.add(rid)
        else:
            empty_id += 1
        if cid:
            unique_cluster_ids.add(cid)
        else:
            empty_cluster += 1
        if total <= 5:
            sample.append({
                "id": rid or "(empty)",
                "cluster_id": cid or "(empty)",
                "type": row.get("type") or "",
                "author_str": row.get("author_str") or "",
                "plain_text_len": len(row.get("plain_text") or ""),
            })

print(f"total rows:           {total:,}")
print(f"unique opinion ids:   {len(unique_ids):,}")
print(f"empty id rows:        {empty_id:,}")
print(f"unique cluster ids:   {len(unique_cluster_ids):,}")
print(f"empty cluster rows:   {empty_cluster:,}")
print(f"\nfirst 5 rows:")
for s in sample:
    print(f"  {s}")
