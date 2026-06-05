"""Load filtered CL bulk subset into DocketDrift models.

Reads the CSVs in ``--subset-dir`` (default ``~/courtlistener-bulk/mn-subset/``)
and upserts into ``Judge`` / ``Opinion`` / ``PanelVote`` rows for the given
state. Phased so we can iterate on one piece at a time::

    python manage.py load_cl_bulk                  # all phases
    python manage.py load_cl_bulk --phase judges
    python manage.py load_cl_bulk --phase opinions --limit 100
    python manage.py load_cl_bulk --dry-run

Idempotency:

- Phase 1 (judges) keys on ``Judge.courtlistener_id`` and FALLS BACK to
  matching by ``state + slug`` -- so existing rows scraped from
  mncourts.gov get linked to their CL identity without duplication.
  Scraper-authoritative fields (full_name, courtlistener_id, court) are
  resynced. The user-curated bio_summary + appointment_date are NEVER
  overwritten on existing rows.
- Phase 2 (opinions) keys on ``Opinion.courtlistener_id``. Existing rows
  are SKIPPED on re-run -- safe to re-launch after a crash. The bulk
  loader does two passes: first creates Opinion rows from clusters with
  metadata only, then streams opinions.csv to fill raw_text.
- Phase 3 (panel) keys on the ``(opinion, judge)`` unique constraint of
  PanelVote -- bulk_create with ignore_conflicts is the natural pattern.

Streaming notes: opinions.csv is 2.3 GB. We never load it into RAM. The
two-pass approach means we read it once for raw_text fill, never holding
more than one row at a time.
"""
from __future__ import annotations

import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from opinions.models import Court, Judge, Opinion, PanelVote, State

# CL bulk fields use backslash escape; raise the field-size cap for big plain_text bodies
csv.field_size_limit(sys.maxsize)

DEFAULT_SUBSET_DIR = str(Path.home() / "courtlistener-bulk" / "mn-subset")

# Map CL court IDs we care about per state. New states slot in here.
STATE_COURT_CL_IDS = {
    "MN": {"minn", "minnctapp"},
    "NH": {"nh"},
}

# Opinion-type preferences for picking which CL Opinion's text becomes the
# DocketDrift Opinion's raw_text. Lower index = higher priority.
# CL types: 010combined, 020lead, 030concurrence, 040dissent, 050addendum,
#           060remand, 070rehearing, 080on-the-merits, 090trial-court-decision
OPINION_TYPE_PRIORITY = {
    "020lead": 0,        # majority opinion -- best
    "010combined": 1,    # combined doc
    "080on-the-merits": 2,
    "060remand": 3,
    "030concurrence": 4,
    "040dissent": 5,
    "050addendum": 6,
    "070rehearing": 7,
    "090trial-court-decision": 8,
}


def _opinion_type_priority(t: str) -> int:
    return OPINION_TYPE_PRIORITY.get(t or "", 99)


def _strip_markup(text: str) -> str:
    """Crude tag-strip for falling back to html/xml when plain_text is empty.

    Good enough for v1; if we later want preserved paragraph structure for
    nicer rendering, we'd swap to BeautifulSoup. v1 just needs searchable
    text.
    """
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _best_text(row: dict) -> str:
    """Pick the best-available body text from an opinions.csv row.

    Falls back through CL's preserved formats. plain_text is preferred
    (already extracted); when missing we strip tags from the next-best
    field.
    """
    plain = row.get("plain_text") or ""
    if plain.strip():
        return plain
    for field in (
        "html_with_citations",  # has citation markup but is the best HTML
        "html_lawbox",
        "html_columbia",
        "html_anon_2020",
        "html",
        "xml_harvard",
        "xml_scan",
    ):
        raw = row.get(field) or ""
        if raw.strip():
            return _strip_markup(raw)
    return ""


def _parse_iso_date(s: str):
    """Parse 'YYYY-MM-DD' (CL's bulk date format). Returns None on bad input."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _open_csv(path: Path):
    """Open one of the filtered mn-subset CSVs.

    The filter script (``scripts/cl_bulk_filter.py``) reads CL's
    backslash-escape source CSVs and writes its mn-subset/ output via
    ``csv.DictWriter`` defaults -- STANDARD CSV with double-quote-doubling
    for embedded quotes. So this loader reads with the standard dialect
    too. Using the backslash-escape dialect here would misparse every
    ``""`` in the output (splitting one logical row into hundreds of
    fragment rows -- bug we hit in v0 of this command).
    """
    fh = open(path, "r", encoding="utf-8", errors="replace", newline="")
    reader = csv.DictReader(
        fh,
        restkey="__extra",
        restval="",
    )
    return reader, fh


class Command(BaseCommand):
    help = "Load filtered CL bulk subset (mn-subset/) into Judge / Opinion / PanelVote."

    def add_arguments(self, parser):
        parser.add_argument(
            "--subset-dir",
            type=str,
            default=DEFAULT_SUBSET_DIR,
            help="Directory of filtered CSVs (default: ~/courtlistener-bulk/mn-subset/).",
        )
        parser.add_argument(
            "--state",
            type=str,
            default="MN",
            help="State code being loaded (default: MN).",
        )
        parser.add_argument(
            "--phase",
            choices=["judges", "opinions", "opinions-text", "panel", "all"],
            default="all",
            help=(
                "Which phase to run. 'opinions' does both metadata + text. "
                "'opinions-text' only re-streams text (skip if already filled)."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N rows of the chosen phase (smoke-test convenience).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse + print counts; no DB writes.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Bulk insert/update batch size (default: 500).",
        )

    def handle(self, *args, subset_dir, state, phase, limit, dry_run, batch_size, **options):
        subset = Path(subset_dir)
        if not subset.exists():
            raise CommandError(f"--subset-dir does not exist: {subset}")

        state_code = state.upper()
        try:
            state_obj = State.objects.get(code=state_code)
        except State.DoesNotExist:
            raise CommandError(
                f"State {state_code!r} not seeded. Run `manage.py migrate` first."
            )

        cl_court_ids = STATE_COURT_CL_IDS.get(state_code)
        if not cl_court_ids:
            raise CommandError(
                f"No CL court IDs configured for state {state_code!r}. "
                "Edit STATE_COURT_CL_IDS in this command."
            )

        self.stdout.write(f"Loading from: {subset}")
        self.stdout.write(f"State: {state_code} ; CL courts: {sorted(cl_court_ids)}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN -- no DB writes."))
        self.stdout.write("")

        if phase in ("judges", "all"):
            self._load_judges(state_obj, subset, cl_court_ids, limit, dry_run)
        if phase in ("opinions", "all"):
            self._load_opinion_metadata(state_obj, subset, cl_court_ids, limit, dry_run, batch_size)
        if phase in ("opinions", "opinions-text", "all"):
            self._load_opinion_text(state_obj, subset, limit, dry_run, batch_size)
        if phase in ("panel", "all"):
            self._load_panel(state_obj, subset, limit, dry_run, batch_size)

        self.stdout.write(self.style.SUCCESS("\nDone."))

    # --- Phase 1: judges ----------------------------------------------------

    def _load_judges(self, state, subset, cl_court_ids, limit, dry_run):
        self.stdout.write("=== Phase 1: judges ===")
        t0 = time.time()

        # Build {cl_person_id: [position_rows]} for positions at MN appellate courts.
        positions_by_person: dict[str, list[dict]] = {}
        reader, fh = _open_csv(subset / "positions.csv")
        try:
            for row in reader:
                pid = row.get("person_id") or ""
                cid = row.get("court_id") or ""
                if pid and cid in cl_court_ids:
                    positions_by_person.setdefault(pid, []).append(row)
        finally:
            fh.close()
        self.stdout.write(f"  found positions for {len(positions_by_person):,} judges")

        courts_by_cl_id = {c.courtlistener_id: c for c in Court.objects.filter(state=state)}

        created = updated = skipped = 0
        used_slugs = set(
            Judge.objects.filter(state=state).values_list("slug", flat=True)
        )

        reader, fh = _open_csv(subset / "people.csv")
        try:
            for i, row in enumerate(reader):
                if limit and (created + updated) >= limit:
                    break

                cl_id = (row.get("id") or "").strip()
                if not cl_id:
                    continue

                # Only load judges who actually had MN appellate positions.
                positions = positions_by_person.get(cl_id)
                if not positions:
                    skipped += 1
                    continue

                name_parts = [
                    (row.get("name_first") or "").strip(),
                    (row.get("name_middle") or "").strip(),
                    (row.get("name_last") or "").strip(),
                ]
                full_name = " ".join(p for p in name_parts if p)
                suffix = (row.get("name_suffix") or "").strip()
                if suffix:
                    full_name = f"{full_name} {suffix}"
                if not full_name:
                    skipped += 1
                    continue

                # Primary court: prefer Supreme over Appeals (Supreme is the
                # final destination in MN appellate practice).
                primary_court = None
                for pos in positions:
                    pcid = pos.get("court_id") or ""
                    if pcid == "minn" or pcid == "nh":
                        primary_court = courts_by_cl_id.get(pcid)
                        break
                if primary_court is None:
                    for pos in positions:
                        primary_court = courts_by_cl_id.get(pos.get("court_id") or "")
                        if primary_court:
                            break

                # Appointment date = earliest non-empty date_start across positions
                # at MN appellate courts.
                appt = None
                for pos in sorted(positions, key=lambda p: p.get("date_start") or ""):
                    d = _parse_iso_date(pos.get("date_start") or "")
                    if d:
                        appt = d
                        break

                if dry_run:
                    self.stdout.write(
                        f"    DRY: {full_name} (cl={cl_id}) "
                        f"-> court={primary_court} appt={appt}"
                    )
                    continue

                # Match: cl_id wins, else state+slug. Slug collision: append cl_id.
                existing = (
                    Judge.objects.filter(state=state, courtlistener_id=cl_id).first()
                    or Judge.objects.filter(state=state, slug=slugify(full_name)).first()
                )

                if existing:
                    # Resync scraper/CL-authoritative fields only. bio_summary,
                    # appointment_date, photo_url stay user-curated.
                    existing.full_name = full_name
                    existing.courtlistener_id = cl_id
                    if primary_court is not None:
                        existing.court = primary_court
                    if appt and not existing.appointment_date:
                        existing.appointment_date = appt
                    existing.save(update_fields=[
                        "full_name", "courtlistener_id", "court", "appointment_date",
                    ])
                    updated += 1
                else:
                    # New row. Build a unique slug.
                    base_slug = slugify(full_name)[:120] or f"judge-{cl_id}"
                    slug = base_slug
                    n = 1
                    while slug in used_slugs:
                        n += 1
                        slug = f"{base_slug}-{n}"
                    used_slugs.add(slug)
                    Judge.objects.create(
                        state=state,
                        court=primary_court,
                        full_name=full_name,
                        slug=slug,
                        role=Judge.Role.UNKNOWN,
                        status=Judge.Status.UNKNOWN,
                        is_currently_seated=False,
                        appointment_date=appt,
                        courtlistener_id=cl_id,
                        source_id=f"cl-{cl_id}",
                    )
                    created += 1
        finally:
            fh.close()

        self.stdout.write(self.style.SUCCESS(
            f"  judges: created={created:,} updated={updated:,} "
            f"skipped={skipped:,} ({time.time()-t0:.1f}s)"
        ))

    # --- Phase 2a: opinion metadata (clusters -> Opinion rows) ---------------

    def _load_opinion_metadata(
        self, state, subset, cl_court_ids, limit, dry_run, batch_size
    ):
        self.stdout.write("\n=== Phase 2a: opinion metadata ===")
        t0 = time.time()

        # Index dockets: cl_docket_id -> (docket_number, cl_court_id)
        dockets: dict[str, tuple[str, str]] = {}
        reader, fh = _open_csv(subset / "dockets.csv")
        try:
            for row in reader:
                dockets[row.get("id") or ""] = (
                    (row.get("docket_number") or "").strip(),
                    (row.get("court_id") or "").strip(),
                )
        finally:
            fh.close()
        self.stdout.write(f"  indexed {len(dockets):,} dockets")

        courts_by_cl_id = {c.courtlistener_id: c for c in Court.objects.filter(state=state)}
        existing_cids = set(
            Opinion.objects.exclude(courtlistener_id="")
            .values_list("courtlistener_id", flat=True)
        )
        self.stdout.write(f"  {len(existing_cids):,} opinions already in DB")

        to_create: list[Opinion] = []
        created = skipped = no_court = no_docket = 0
        seen = 0

        reader, fh = _open_csv(subset / "opinion-clusters.csv")
        try:
            for row in reader:
                seen += 1
                if seen % 10_000 == 0:
                    self.stdout.write(
                        f"    scanned {seen:,} clusters, queued {len(to_create):,}, "
                        f"created {created:,} ({time.time()-t0:.0f}s)",
                        ending="\n",
                    )

                if limit and (created + len(to_create)) >= limit:
                    break

                cluster_id = (row.get("id") or "").strip()
                if not cluster_id or cluster_id in existing_cids:
                    skipped += 1
                    continue

                docket_id = (row.get("docket_id") or "").strip()
                docket = dockets.get(docket_id)
                if not docket:
                    no_docket += 1
                    continue
                docket_number, cl_court_id = docket
                if cl_court_id not in cl_court_ids:
                    skipped += 1
                    continue
                court = courts_by_cl_id.get(cl_court_id)
                if not court:
                    no_court += 1
                    continue

                title = (
                    row.get("case_name")
                    or row.get("case_name_full")
                    or row.get("case_name_short")
                    or ""
                ).strip()[:2048]
                if not title:
                    skipped += 1
                    continue

                release_date = _parse_iso_date(row.get("date_filed") or "")
                if not release_date:
                    skipped += 1
                    continue

                disposition = (row.get("disposition") or "").strip()[:128]
                precedential = (row.get("precedential_status") or "").strip().lower()

                if dry_run:
                    continue

                # Avoid triggering Opinion.save() (parser hook, disposition bucket)
                # by using bulk_create. We compute the bucket here.
                from opinions.utils import compute_disposition_bucket
                op = Opinion(
                    court=court,
                    case_number=docket_number[:64] or f"cl-{cluster_id}",
                    title=title,
                    release_date=release_date,
                    is_precedential=precedential == "published",
                    disposition=disposition,
                    disposition_bucket=compute_disposition_bucket(disposition),
                    raw_text="",  # filled in phase 2b
                    courtlistener_id=cluster_id,
                    review_status=Opinion.ReviewStatus.AI_ONLY,
                )
                to_create.append(op)

                if len(to_create) >= batch_size:
                    Opinion.objects.bulk_create(to_create, ignore_conflicts=True)
                    created += len(to_create)
                    to_create.clear()
        finally:
            fh.close()

        if to_create and not dry_run:
            Opinion.objects.bulk_create(to_create, ignore_conflicts=True)
            created += len(to_create)

        self.stdout.write(self.style.SUCCESS(
            f"  clusters scanned: {seen:,}"
        ))
        self.stdout.write(self.style.SUCCESS(
            f"  opinions created: {created:,}  skipped: {skipped:,}  "
            f"missing-docket: {no_docket:,}  missing-court: {no_court:,}  "
            f"({time.time()-t0:.1f}s)"
        ))

    # --- Phase 2b: opinion text (streams 2.3 GB opinions.csv) ----------------

    def _load_opinion_text(self, state, subset, limit, dry_run, batch_size):
        self.stdout.write("\n=== Phase 2b: opinion text (streams 2.3 GB opinions.csv) ===")
        t0 = time.time()

        # Pre-fetch all opinion PKs by courtlistener_id (= cluster_id) for fast lookup.
        # ~71K rows, small map, ~5MB.
        opinion_pk_by_cluster_cid: dict[str, int] = dict(
            Opinion.objects.exclude(courtlistener_id="")
            .filter(court__state=state, raw_text="")
            .values_list("courtlistener_id", "pk")
        )
        self.stdout.write(
            f"  {len(opinion_pk_by_cluster_cid):,} opinions need text filled"
        )
        if not opinion_pk_by_cluster_cid:
            self.stdout.write("  nothing to do.")
            return

        # We pick the highest-priority opinion type per cluster. Track best
        # seen so far per cluster_id while streaming.
        # Storing (priority, text) in a dict can blow memory for ~71K x 30KB text.
        # Instead: stream opinions, batch-flush updates as we go.
        # We do retain best-priority-seen per cluster to avoid overwriting a
        # better text with a worse one.
        # Memory: best_priority_seen has 71K entries x small ints = trivial.
        # Updates batch out per N rows to keep memory bounded.

        best_priority: dict[str, int] = {}
        pending_updates: list[Opinion] = []
        updated = scanned = 0

        reader, fh = _open_csv(subset / "opinions.csv")
        try:
            for row in reader:
                scanned += 1
                if scanned % 5_000 == 0:
                    self.stdout.write(
                        f"    scanned {scanned:,} opinions, updated {updated:,} "
                        f"({time.time()-t0:.0f}s)"
                    )

                cluster_cid = (row.get("cluster_id") or "").strip()
                pk = opinion_pk_by_cluster_cid.get(cluster_cid)
                if pk is None:
                    continue  # opinion belongs to a cluster we don't care about

                text = _best_text(row)
                if not text:
                    continue

                priority = _opinion_type_priority(row.get("type") or "")
                if cluster_cid in best_priority and best_priority[cluster_cid] <= priority:
                    continue  # we've already seen a same- or higher-priority text
                best_priority[cluster_cid] = priority

                if dry_run:
                    continue

                pending_updates.append(
                    Opinion(pk=pk, raw_text=text[:5_000_000])
                )

                if len(pending_updates) >= batch_size:
                    Opinion.objects.bulk_update(pending_updates, ["raw_text"])
                    updated += len(pending_updates)
                    pending_updates.clear()

                if limit and updated >= limit:
                    break
        finally:
            fh.close()

        if pending_updates and not dry_run:
            Opinion.objects.bulk_update(pending_updates, ["raw_text"])
            updated += len(pending_updates)

        self.stdout.write(self.style.SUCCESS(
            f"  text-filled: {updated:,}  scanned: {scanned:,}  ({time.time()-t0:.1f}s)"
        ))

    # --- Phase 3: panel votes -----------------------------------------------

    def _load_panel(self, state, subset, limit, dry_run, batch_size):
        self.stdout.write("\n=== Phase 3: panel votes ===")
        t0 = time.time()

        # Build lookups -- both are bounded small.
        opinion_pk_by_cluster_cid = dict(
            Opinion.objects.exclude(courtlistener_id="")
            .filter(court__state=state)
            .values_list("courtlistener_id", "pk")
        )
        judge_pk_by_cl_id = dict(
            Judge.objects.exclude(courtlistener_id="")
            .filter(state=state)
            .values_list("courtlistener_id", "pk")
        )
        self.stdout.write(
            f"  {len(opinion_pk_by_cluster_cid):,} opinions, "
            f"{len(judge_pk_by_cl_id):,} judges available for join"
        )

        existing_pairs = set(
            PanelVote.objects.filter(opinion__court__state=state)
            .values_list("opinion_id", "judge_id")
        )
        self.stdout.write(f"  {len(existing_pairs):,} panel rows already in DB")

        to_create: list[PanelVote] = []
        created = skipped = 0
        reader, fh = _open_csv(subset / "panel.csv")
        try:
            for row in reader:
                if limit and created >= limit:
                    break
                cluster_cid = (row.get("opinioncluster_id") or "").strip()
                person_cid = (row.get("person_id") or "").strip()
                op_pk = opinion_pk_by_cluster_cid.get(cluster_cid)
                judge_pk = judge_pk_by_cl_id.get(person_cid)
                if not (op_pk and judge_pk):
                    skipped += 1
                    continue
                if (op_pk, judge_pk) in existing_pairs:
                    skipped += 1
                    continue
                # CL panel join doesn't distinguish role; default to "joined majority".
                # We can derive author later from opinions.author_id when wiring J3.
                to_create.append(PanelVote(
                    opinion_id=op_pk,
                    judge_id=judge_pk,
                    vote_type=PanelVote.Vote.MAJORITY_JOIN,
                ))
                existing_pairs.add((op_pk, judge_pk))

                if len(to_create) >= batch_size:
                    if not dry_run:
                        PanelVote.objects.bulk_create(to_create, ignore_conflicts=True)
                    created += len(to_create)
                    to_create.clear()
        finally:
            fh.close()

        if to_create and not dry_run:
            PanelVote.objects.bulk_create(to_create, ignore_conflicts=True)
            created += len(to_create)

        self.stdout.write(self.style.SUCCESS(
            f"  panel rows created: {created:,}  skipped: {skipped:,}  "
            f"({time.time()-t0:.1f}s)"
        ))
