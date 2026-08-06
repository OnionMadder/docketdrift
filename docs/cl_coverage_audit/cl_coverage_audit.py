"""Every-state CL coverage audit: find MN-shaped ingestion holes.

Method proven on Minnesota: read CourtListener's own bulk export, count
clusters per court per year, and watch two tells --
  1. a year count cratering against the court's own baseline, and
  2. the UNPUBLISHED stream dying first (MN 2018: 213 rows, all Published,
     where 2016 was 1,138 Unpublished of 1,451).

This produces CANDIDATES, not verdicts: a real filing decline and an ingest
failure look identical from CL's side alone. Confirming any hit means checking
the court's actual output (their site / annual reports) -- attended work,
one state at a time, exactly like MN.

Runs locally against the 2026-03-31 bulk export. Streaming, RAM-light.
2026 is excluded (partial year in this snapshot); 2025 is complete.
"""
import bz2
import csv
import sys
import time
import collections
import statistics

BULK = "C:/Users/kelly/courtlistener-bulk"
OUT = sys.argv[1] if len(sys.argv) > 1 else "cl_coverage_summary.csv"

csv.field_size_limit(2**31 - 1)

# --- pass A: state supreme (S) + state appellate (SA) courts -------------
courts = {}
with bz2.open(f"{BULK}/courts-2026-03-31.csv.bz2", "rt",
              encoding="utf8", errors="ignore") as f:
    for row in csv.reader(f):
        if row and row[17] in ("S", "SA"):
            courts[row[0]] = (row[17], row[13])
print("state appellate/supreme courts: %d" % len(courts), flush=True)

# --- pass B: docket_id -> court_id for those courts ----------------------
t0 = time.time()
d2c = {}
with bz2.open(f"{BULK}/dockets-2026-03-31.csv.bz2", "rt",
              encoding="utf8", errors="ignore") as f:
    r = csv.reader(f)
    next(r)
    for i, row in enumerate(r):
        if i % 5_000_000 == 0:
            print("  dockets %dM (%.0fs)" % (i / 1e6, time.time() - t0), flush=True)
        try:
            if row[42] in courts:
                d2c[int(row[0])] = row[42]
        except (IndexError, ValueError):
            continue
print("state-court dockets: %d (%.0fs)" % (len(d2c), time.time() - t0), flush=True)

# --- pass C: clusters -> counts[court][year][pub|unpub] ------------------
t0 = time.time()
counts = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
with bz2.open(f"{BULK}/opinion-clusters-2026-03-31.csv.bz2", "rt",
              encoding="utf8", errors="ignore") as f:
    r = csv.reader(f)
    next(r)
    for i, row in enumerate(r):
        if i % 2_000_000 == 0:
            print("  clusters %dM (%.0fs)" % (i / 1e6, time.time() - t0), flush=True)
        try:
            court = d2c.get(int(row[33]))
        except (IndexError, ValueError):
            continue
        if court is None:
            continue
        year = row[4][:4]
        if not year.isdigit():
            continue
        y = int(year)
        if y < 1990 or y > 2025:
            continue
        slot = counts[court][y]
        if row[28] == "Published":
            slot[0] += 1
        else:
            slot[1] += 1
print("clusters aggregated (%.0fs)" % (time.time() - t0), flush=True)

# --- summary CSV ---------------------------------------------------------
with open(OUT, "w", newline="", encoding="utf8") as f:
    w = csv.writer(f)
    w.writerow(["court", "jurisdiction", "name", "year", "published", "unpublished"])
    for court in sorted(counts):
        j, name = courts[court]
        for y in sorted(counts[court]):
            p, u = counts[court][y]
            w.writerow([court, j, name, y, p, u])
print("summary -> %s" % OUT, flush=True)

# --- anomaly report ------------------------------------------------------
print("\n=== MN-SHAPED ANOMALIES (baseline = median 2012-2019, >=100/yr) ===",
      flush=True)
findings = []
for court, years in counts.items():
    base_years = [sum(years[y]) for y in range(2012, 2020) if y in years]
    if len(base_years) < 5:
        continue
    baseline = statistics.median(base_years)
    if baseline < 100:
        continue
    base_unpub = statistics.median(
        [years[y][1] / max(1, sum(years[y]))
         for y in range(2012, 2020) if y in years])
    for y in (2023, 2024, 2025):
        tot = sum(years.get(y, (0, 0)))
        ratio = tot / baseline
        if ratio < 0.6:
            unpub_share = years.get(y, (0, 0))[1] / max(1, tot)
            unpub_died = base_unpub >= 0.30 and unpub_share < 0.10
            findings.append((baseline * (1 - ratio), court, courts[court][1],
                             y, int(baseline), tot, ratio, unpub_died))

findings.sort(reverse=True)
print("%-14s %-38s %s %9s %7s %6s %s" %
      ("court", "name", "year", "baseline", "actual", "ratio", "unpub-died"))
seen = set()
for sev, court, name, y, base, tot, ratio, ud in findings[:40]:
    print("%-14s %-38s %d %9d %7d %5.0f%% %s" %
          (court, name[:38], y, base, tot, 100 * ratio,
           "  <<<" if ud else ""))
    seen.add(court)
print("\ndistinct courts flagged: %d" % len(seen), flush=True)
