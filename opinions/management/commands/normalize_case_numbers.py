"""Rewrite malformed case numbers to their canonical form, IN PLACE.

Why this exists: `case_number` is the URL key and the thing a paste-a-docket
search matches, so a row stored as 'a230380' or 'No. A15-1566' is unreachable
by the identifier a lawyer would actually type. Those forms were inherited from
CourtListener (their MN dockets.csv stores 'A211648' / 'a241471') and from
opinion text that carries a "No. " prefix.

SAFETY -- this command will not merge, delete, or fuse anything:

  * It renames ONLY when the canonical form does not already exist in the same
    court. If it does exist, the pair is a COLLISION and is skipped and
    reported, never merged. A 2026-08-04 dry run classified 1,482 such pairs:
    974 look like true duplicates but **508 are two genuinely different
    opinions filed on one docket** (an opinion and a later amended opinion or
    order). Merging on docket number alone would have destroyed those 508.
    Deduplication is a separate, deliberate pass -- not a side effect of a
    formatting fix.
  * Two rows that would both become the same value are both skipped, so the
    command can never introduce a new collision either.
  * Old URLs keep working: `opinion_detail` / `opinion_pdf` canonicalize the
    requested docket and retry, using the SAME function this command applies.

Dry-run by default. Pass --apply to write.

NFSN: lifts max_statement_time (batch work inherits the 25s web cap) and is
chunked/resumable via --max-runtime + --min-id so it self-exits under the CPU
cull.
"""
import time

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from opinions.case_numbers import canonical_case_number
from opinions.models import Opinion, State


class Command(BaseCommand):
    help = "Rewrite malformed case_numbers to canonical form (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--state", default="",
                            help="Limit to one state (USPS code). Default: all live states.")
        parser.add_argument("--apply", action="store_true",
                            help="Actually write. Without this, nothing is modified.")
        parser.add_argument("--max-runtime", type=int, default=0,
                            help="Self-exit after N seconds (NFSN CPU cull ~40s). 0 = unbounded.")
        parser.add_argument("--min-id", type=int, default=0,
                            help="Resume from this opinion id (printed on time-bound exit).")
        parser.add_argument("--id-batch", type=int, default=5000,
                            help="Opinion ids to fetch per window.")

    def handle(self, *args, **opts):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        states = State.objects.filter(is_live=True)
        if opts["state"]:
            states = State.objects.filter(code=opts["state"].upper())
        court_ids = []
        for st in states:
            court_ids.extend(st.courts.values_list("id", flat=True))
        if not court_ids:
            self.stderr.write("no courts matched")
            return

        # Existing values per court -- the collision guard. Built from
        # case_number alone (no TEXT columns): pulling raw_text corpus-wide is
        # the documented way to get this connection dropped.
        existing = {}
        for cid, cn in Opinion.objects.filter(
                court_id__in=court_ids).values_list("court_id", "case_number"):
            existing.setdefault(cid, set()).add((cn or "").strip())

        # Pre-compute which canonical values two different rows both want, so
        # we never turn a rename into a new collision.
        wanted = {}
        for cid, cn in Opinion.objects.filter(
                court_id__in=court_ids).values_list("court_id", "case_number"):
            s = (cn or "").strip()
            new = canonical_case_number(s)
            if new != s:
                wanted.setdefault(cid, {}).setdefault(new, 0)
                wanted[cid][new] += 1

        started = time.time()
        apply_ = opts["apply"]
        min_id = opts["min_id"]
        renamed = skipped_collision = skipped_internal = 0
        samples, collisions = [], []
        last_id = min_id

        while True:
            batch = list(Opinion.objects.filter(
                court_id__in=court_ids, id__gte=last_id
            ).order_by("id").values_list(
                "id", "court_id", "case_number")[:opts["id_batch"]])
            if not batch:
                break
            for oid, cid, cn in batch:
                last_id = oid + 1
                s = (cn or "").strip()
                new = canonical_case_number(s)
                if new == s:
                    continue
                if wanted.get(cid, {}).get(new, 0) > 1:
                    skipped_internal += 1
                    continue
                if new in existing.get(cid, ()):
                    skipped_collision += 1
                    if len(collisions) < 10:
                        collisions.append("court=%s %r -> %s (exists)" % (cid, s, new))
                    continue
                if len(samples) < 12:
                    samples.append("%-24r -> %s" % (s, new))
                if apply_:
                    for attempt in (1, 2, 3):
                        try:
                            with transaction.atomic():
                                Opinion.objects.filter(id=oid).update(case_number=new)
                            break
                        except BaseException:
                            # MariaDB drops long-idle connections during batch
                            # work; reconnect and retry rather than lose the run.
                            if attempt == 3:
                                raise
                            connection.close()
                            time.sleep(2)
                    existing.setdefault(cid, set()).add(new)
                    existing[cid].discard(s)
                renamed += 1

            if opts["max_runtime"] and (time.time() - started) > opts["max_runtime"]:
                self.stdout.write(self.style.WARNING(
                    "  time budget hit; resume with:  --min-id %d" % last_id))
                break

        verb = "renamed" if apply_ else "WOULD rename"
        self.stdout.write("")
        self.stdout.write("  %-34s %6d" % (verb, renamed))
        self.stdout.write("  %-34s %6d" % ("skipped (canonical exists)", skipped_collision))
        self.stdout.write("  %-34s %6d" % ("skipped (two rows want it)", skipped_internal))
        if samples:
            self.stdout.write("  -- sample rewrites --")
            for s in samples:
                self.stdout.write("     %s" % s)
        if collisions:
            self.stdout.write("  -- sample collisions (left alone) --")
            for c in collisions:
                self.stdout.write("     %s" % c)
        if not apply_:
            self.stdout.write(self.style.WARNING(
                "\n  DRY RUN -- nothing written. Re-run with --apply."))
