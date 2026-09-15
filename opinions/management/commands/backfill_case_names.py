"""Recompute ``Opinion.title`` from the state parser for existing rows.

WHY THIS EXISTS
---------------
The MN caption extractor kept only the FIRST paragraph block of a caption,
and a regular opinion's caption wraps across blank lines exactly like an
order's does:

    Marcel Moses, petitioner,
    Appellant,

    vs.

    State of Minnesota,
    Respondent.

So thousands of Minnesota opinions were titled with one side of the caption
-- the appellant alone, or nothing but "In re the Marriage of:". Reported
2026-09-14 by a party who noticed his own page named him and not the State.
The parser is fixed; this command repairs the rows already stored.

SAFETY
------
This rewrites the visible title, the ``<title>`` tag and the og: metadata of
public pages, so it is deliberately cautious:

* **Dry-run by default.** ``--apply`` to commit.
* **Never writes an empty name.** A parse that returns nothing leaves the
  stored title alone -- blank is not an improvement.
* **Never silently SHRINKS a title** by more than ``--max-shrink`` percent.
  The whole point of the fix is that captions were being cut short; a new
  name much shorter than the old one means the parser got worse, not
  better, and that row is reported rather than written. (This guard is what
  would have caught the trailing-period bug on "The Emily Program, P.C."
  had it reached this stage.)
* Cull-safe: ``--max-runtime`` self-exits at a window boundary and prints
  the shared ``resume with:  --min-id N`` trailer (DOUBLE space -- wrappers
  grep that exact string).

Usage::

    python manage.py backfill_case_names --state MN --dry-run
    python manage.py backfill_case_names --state MN --apply --max-runtime 300
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Court, Opinion


class Command(BaseCommand):
    help = "Recompute Opinion.title from the state parser for existing rows."

    def add_arguments(self, parser):
        parser.add_argument("--state", required=True,
                            help="State code (e.g. MN).")
        parser.add_argument("--apply", action="store_true",
                            help="Commit. Omit for a dry run.")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--min-id", type=int, default=0)
        parser.add_argument("--max-runtime", type=int, default=0,
                            help="Self-exit after N seconds at a window "
                                 "boundary. ~300 is cull-safe on NFSN.")
        parser.add_argument("--max-shrink", type=int, default=40,
                            help="Refuse a new title more than N%% shorter "
                                 "than the stored one (default 40).")

    def handle(self, *args, state, apply, limit, min_id, max_runtime,
               max_shrink, **options):
        from opinions.parsing import parse as parse_opinion

        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        court_ids = list(
            Court.objects.filter(state__code=state.upper())
            .values_list("id", flat=True)
        )
        if not court_ids:
            self.stderr.write("no courts for state %s" % state)
            return

        base = (Opinion.objects.filter(court_id__in=court_ids)
                .exclude(raw_text=""))

        self.stdout.write(self.style.SUCCESS(
            "Recomputing case names for %s%s"
            % (state.upper(), "" if apply else "  (DRY RUN)")))

        scanned = changed = unchanged = blank = refused = 0
        shrink_examples = []
        pending = []
        last_pk = int(min_id or 0)
        t0 = time.time()
        stopped_early = False
        SCAN = 400

        def flush():
            if pending and apply:
                Opinion.objects.bulk_update(pending, ["title"])
            pending.clear()

        def trailer():
            self.stdout.write("resume with:  --min-id %d" % last_pk)

        while True:
            if limit and scanned >= limit:
                break
            if max_runtime and (time.time() - t0) >= max_runtime:
                stopped_early = True
                break

            batch = list(base.filter(pk__gt=last_pk).order_by("pk")
                         .only("id", "title", "raw_text")[:SCAN])
            if not batch:
                break

            for op in batch:
                if limit and scanned >= limit:
                    break
                scanned += 1
                last_pk = op.pk

                result = parse_opinion(state.upper(), op.raw_text)
                new = ((result.case_name if result else None) or "").strip()
                old = (op.title or "").strip()

                if not new:
                    blank += 1
                    continue
                if new == old:
                    unchanged += 1
                    continue
                # Guard: a much SHORTER name means the parser regressed.
                if old and len(new) < len(old) * (1 - max_shrink / 100.0):
                    refused += 1
                    if len(shrink_examples) < 5:
                        shrink_examples.append((op.pk, old[:60], new[:60]))
                    continue

                changed += 1
                op.title = new[:500]
                pending.append(op)
                if len(pending) >= 200:
                    flush()

            if scanned // 2_000 > (scanned - len(batch)) // 2_000:
                # Flush + emit the trailer at each progress mark so a CPU
                # cull (rc=152) resumes near the death point, not the start.
                flush()
                self.stdout.write(
                    "  scanned %6d  changed %5d  unchanged %5d  blank %5d"
                    % (scanned, changed, unchanged, blank))
                trailer()

        flush()
        tag = "  (stopped early on --max-runtime)" if stopped_early else ""
        self.stdout.write(self.style.SUCCESS(
            "\nDone in %.1f min.%s" % ((time.time() - t0) / 60.0, tag)))
        self.stdout.write(
            "  scanned:   %7d\n"
            "  changed:   %7d%s\n"
            "  unchanged: %7d\n"
            "  no parse:  %7d\n"
            "  REFUSED (would shrink >%d%%): %d"
            % (scanned, changed, "" if apply else " (dry run, not written)",
               unchanged, blank, max_shrink, refused))
        for pk, old, new in shrink_examples:
            self.stdout.write("    id=%s\n      was %r\n      now %r"
                              % (pk, old, new))
        trailer()
