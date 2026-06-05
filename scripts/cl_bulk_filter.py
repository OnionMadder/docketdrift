"""Filter CourtListener bulk dumps to a Minnesota appellate subset.

Reads every .csv.bz2 file in ~/courtlistener-bulk/ that we need, filters
to rows tied to MN (court_id IN ('minn','minnctapp') for the entry points;
the rest cascade through the FK graph), and writes the trimmed CSVs to
~/courtlistener-bulk/mn-subset/. The trimmed subset is what the
``load_cl_bulk`` Django command reads -- small enough (~1-2 GB) to SCP
to NFSN, and keeps the schema 1:1 with CL so we never re-process the
50 GB raw opinions file again.

Filter chain (each numbered step writes a CSV + may collect ids the
subsequent steps depend on):

    1. dockets         WHERE court_id IN MN          -> mn_docket_ids
    2. opinion-clusters WHERE docket_id IN ^         -> mn_cluster_ids
    3. opinions        WHERE cluster_id IN ^         -> mn_opinion_ids
                       (THE 50 GB SWEEP -- 1-2 hours)
    4. panel join      WHERE opinioncluster_id IN ^  -> + mn_judge_ids
    5. positions       WHERE court_id IN MN          -> + mn_judge_ids
    6. joined_by       WHERE opinion_id IN ^
    7. people          WHERE id IN mn_judge_ids
    8. educations      WHERE person_id IN ^          -> mn_school_ids
    9. political-aff   WHERE person_id IN ^
    10. races          WHERE person_id IN ^
    11. schools        WHERE id IN mn_school_ids
    12. courts         copy ALL (3K rows of reference data)

Re-runnable: overwrites mn-subset/ on each invocation. The bz2 source
files are read-only and never modified.

Run from project root:

    .venv/Scripts/python scripts/cl_bulk_filter.py
"""

import bz2
import csv
import sys
import time
from pathlib import Path

BULK_DIR = Path(r"C:\Users\kelly\courtlistener-bulk")
OUT_DIR = BULK_DIR / "mn-subset"
SNAPSHOT = "2026-03-31"
MN_COURT_IDS = {"minn", "minnctapp"}

# CL's quoted plain_text rows can be megabytes; raise the stdlib cap.
csv.field_size_limit(sys.maxsize)


def src(table: str) -> Path:
    return BULK_DIR / f"{table}-{SNAPSHOT}.csv.bz2"


def dst(name: str) -> Path:
    return OUT_DIR / f"{name}.csv"


def filter_stream(
    table: str,
    out_name: str,
    predicate,
    *,
    collect_field: str | None = None,
    progress_every: int = 1_000_000,
):
    """Stream a bz2 CSV; write predicate-matching rows to mn-subset.

    Returns set of values pulled from collect_field (or empty set).
    Bad rows (csv.Error / UnicodeDecodeError handled at the bz2 layer via
    errors='replace') are skipped with a counter.
    """
    path = src(table)
    if not path.exists():
        sys.exit(f"Missing source file: {path}")

    out = dst(out_name)
    collected: set[str] = set()
    matched = 0
    seen = 0
    skipped = 0
    t0 = time.time()

    with bz2.open(path, "rt", encoding="utf-8", errors="replace", newline="") as src_fh, \
         open(out, "w", encoding="utf-8", newline="") as out_fh:
        # CL bulk CSVs are emitted by PostgreSQL COPY with ESCAPE '\' (per
        # load-bulk-data-*.sh) -- backslash escapes embedded quotes/backslashes
        # rather than the standard CSV double-quote-doubling convention.
        # Python's csv module defaults the other way, which silently corrupts
        # any row whose plain_text/xml field contains \" -- so we configure the
        # reader to match CL's dialect explicitly.
        # restkey/restval handle malformed rows whose field count drifts from
        # the header (over/under). DictWriter stays default so our OUTPUT uses
        # standard CSV that downstream tools can read without special config.
        reader = csv.DictReader(
            src_fh,
            quotechar='"',
            escapechar="\\",
            doublequote=False,
            restkey="__extra",
            restval="",
        )
        writer = csv.DictWriter(out_fh, fieldnames=reader.fieldnames)
        writer.writeheader()

        row_iter = iter(reader)
        while True:
            try:
                row = next(row_iter)
            except StopIteration:
                break
            except csv.Error:
                skipped += 1
                continue

            seen += 1
            if seen % progress_every == 0:
                elapsed = time.time() - t0
                rate = seen / elapsed if elapsed > 0 else 0
                print(
                    f"    [{table}] scanned {seen:>10,}  kept {matched:>8,}  "
                    f"({rate:>6,.0f} rows/s, {elapsed:>4.0f}s)",
                    flush=True,
                )

            if predicate(row):
                row.pop("__extra", None)  # drop overflow fields, if any
                writer.writerow(row)
                matched += 1
                if collect_field:
                    v = row.get(collect_field)
                    if v:
                        collected.add(v)

    elapsed = time.time() - t0
    extra = f", skipped {skipped:,} bad" if skipped else ""
    print(
        f"  [{table}] scanned {seen:,} -> kept {matched:,}{extra} -> "
        f"{out.name} ({elapsed:.1f}s)",
        flush=True,
    )
    return collected


def copy_stream(table: str, out_name: str):
    """Decompress a bz2 CSV verbatim to mn-subset/ (no filtering)."""
    path = src(table)
    if not path.exists():
        sys.exit(f"Missing source file: {path}")
    out = dst(out_name)
    t0 = time.time()
    rows = 0
    with bz2.open(path, "rt", encoding="utf-8", errors="replace", newline="") as src_fh, \
         open(out, "w", encoding="utf-8", newline="") as out_fh:
        # Same CL backslash-escape dialect as filter_stream (see comment there).
        reader = csv.DictReader(
            src_fh,
            quotechar='"',
            escapechar="\\",
            doublequote=False,
        )
        writer = csv.DictWriter(out_fh, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            writer.writerow(row)
            rows += 1
    print(f"  [{table}] copied {rows:,} rows -> {out.name} ({time.time()-t0:.1f}s)")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output: {OUT_DIR}")
    print(f"MN courts: {sorted(MN_COURT_IDS)}")
    print(f"Snapshot:  {SNAPSHOT}\n")
    grand_t0 = time.time()

    # ----- 1. dockets ---------------------------------------------------------
    print("=== 1/12  dockets ===")
    mn_docket_ids = filter_stream(
        "dockets", "dockets",
        predicate=lambda r: r.get("court_id") in MN_COURT_IDS,
        collect_field="id",
    )
    print(f"  -> {len(mn_docket_ids):,} MN docket ids\n")

    # ----- 2. opinion-clusters ------------------------------------------------
    print("=== 2/12  opinion-clusters ===")
    mn_cluster_ids = filter_stream(
        "opinion-clusters", "opinion-clusters",
        predicate=lambda r: r.get("docket_id") in mn_docket_ids,
        collect_field="id",
    )
    print(f"  -> {len(mn_cluster_ids):,} MN cluster ids\n")

    # ----- 3. opinions (THE BIG SWEEP) ----------------------------------------
    print("=== 3/12  opinions  (50 GB sweep -- this is the long one) ===")
    mn_opinion_ids = filter_stream(
        "opinions", "opinions",
        predicate=lambda r: r.get("cluster_id") in mn_cluster_ids,
        collect_field="id",
        progress_every=500_000,
    )
    print(f"  -> {len(mn_opinion_ids):,} MN opinion ids\n")

    # ----- 4. panel join (collects judge_ids) ---------------------------------
    print("=== 4/12  search_opinioncluster_panel ===")
    mn_judge_ids = filter_stream(
        "search_opinioncluster_panel", "panel",
        predicate=lambda r: r.get("opinioncluster_id") in mn_cluster_ids,
        collect_field="person_id",
        progress_every=200_000,
    )
    print(f"  -> {len(mn_judge_ids):,} judge ids from panel\n")

    # ----- 5. positions (court_id IN MN; also collects judge_ids) -------------
    print("=== 5/12  people-db-positions ===")
    pos_judge_ids = filter_stream(
        "people-db-positions", "positions",
        predicate=lambda r: r.get("court_id") in MN_COURT_IDS,
        collect_field="person_id",
        progress_every=200_000,
    )
    mn_judge_ids |= pos_judge_ids
    print(
        f"  -> {len(pos_judge_ids):,} judge ids from positions ; "
        f"total unique judges: {len(mn_judge_ids):,}\n"
    )

    # ----- 6. joined_by (concurrences) ----------------------------------------
    print("=== 6/12  search_opinion_joined_by ===")
    filter_stream(
        "search_opinion_joined_by", "joined-by",
        predicate=lambda r: r.get("opinion_id") in mn_opinion_ids,
        progress_every=10_000,
    )
    print()

    # ----- 7. people ----------------------------------------------------------
    print("=== 7/12  people-db-people ===")
    filter_stream(
        "people-db-people", "people",
        predicate=lambda r: r.get("id") in mn_judge_ids,
        progress_every=100_000,
    )
    print()

    # ----- 8. educations (collects school_ids) --------------------------------
    print("=== 8/12  people-db-educations ===")
    mn_school_ids = filter_stream(
        "people-db-educations", "educations",
        predicate=lambda r: r.get("person_id") in mn_judge_ids,
        collect_field="school_id",
        progress_every=100_000,
    )
    print(f"  -> {len(mn_school_ids):,} school ids referenced\n")

    # ----- 9. political affiliations ------------------------------------------
    print("=== 9/12  people-db-political-affiliations ===")
    filter_stream(
        "people-db-political-affiliations", "political-affiliations",
        predicate=lambda r: r.get("person_id") in mn_judge_ids,
        progress_every=50_000,
    )
    print()

    # ----- 10. races ----------------------------------------------------------
    print("=== 10/12  people-db-races ===")
    filter_stream(
        "people-db-races", "races",
        predicate=lambda r: r.get("person_id") in mn_judge_ids,
        progress_every=10_000,
    )
    print()

    # ----- 11. schools (filter by referenced ids) -----------------------------
    print("=== 11/12  people-db-schools ===")
    filter_stream(
        "people-db-schools", "schools",
        predicate=lambda r: r.get("id") in mn_school_ids,
        progress_every=10_000,
    )
    print()

    # ----- 12. courts (reference data, copy all 3K rows) ----------------------
    print("=== 12/12  courts (full copy of all 3K reference rows) ===")
    copy_stream("courts", "courts")
    print()

    grand_elapsed = time.time() - grand_t0
    print("=" * 60)
    print(f"DONE in {grand_elapsed/60:.1f} min")
    print("-" * 60)
    print(f"  MN dockets:           {len(mn_docket_ids):>8,}")
    print(f"  MN clusters:          {len(mn_cluster_ids):>8,}")
    print(f"  MN opinions:          {len(mn_opinion_ids):>8,}")
    print(f"  MN judges (unique):   {len(mn_judge_ids):>8,}")
    print(f"  schools referenced:   {len(mn_school_ids):>8,}")
    print()
    print(f"Output: {OUT_DIR}")
    print("Next: SCP mn-subset/ to NFSN + run manage.py load_cl_bulk")


if __name__ == "__main__":
    main()
