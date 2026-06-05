"""Print column names + a sample first row from every CL bulk file.

We need this map to chain the MN filter across tables. Specifically we want
to confirm:

* opinion-clusters references docket_id (so we have to join through dockets
  to reach court_id)
* opinions references cluster_id
* search_opinioncluster_panel references opinioncluster_id + person_id
* people-db-positions references person_id

Long fields (notably opinions.plain_text) are truncated to 100 chars so
the dump stays readable.
"""
import bz2
import csv
import sys
from pathlib import Path

BULK_DIR = Path(r"C:\Users\kelly\courtlistener-bulk")
SNAPSHOT = "2026-03-31"

TABLES = [
    "dockets",
    "opinion-clusters",
    "opinions",
    "search_opinioncluster_panel",
    "search_opinion_joined_by",
    "people-db-people",
    "people-db-positions",
    "people-db-educations",
    "people-db-political-affiliations",
    "people-db-schools",
    "people-db-races",
]

csv.field_size_limit(50_000_000)


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def truncate(s: str, n: int = 100) -> str:
    s = s or ""
    if len(s) > n:
        return s[:n] + f"...[+{len(s)-n}]"
    return s


for table in TABLES:
    path = BULK_DIR / f"{table}-{SNAPSHOT}.csv.bz2"
    if not path.exists():
        print(f"=== {table} === MISSING ({path})\n")
        continue

    size = path.stat().st_size
    print(f"=== {table}  ({fmt_bytes(size)} compressed) ===")
    with bz2.open(path, "rt", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        print(f"  columns ({len(cols)}):")
        # 4-per-line for readability
        for i in range(0, len(cols), 4):
            print(f"    {cols[i:i+4]}")
        try:
            row = next(reader)
            print("  sample row 1:")
            for k in cols:
                v = truncate(row.get(k, ""))
                print(f"    {k} = {v!r}")
        except StopIteration:
            print("  (no rows)")
    print()
