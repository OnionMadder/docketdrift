"""Refresh ``Judge.first_vote_date`` / ``last_vote_date`` from panel votes.

A judge's active span is MIN/MAX ``release_date`` across the opinions they
sat on. Computing that on the request path means joining
``opinions_panelvote`` to the 2.75GB ``opinions_opinion`` table and reading
a column no index covers, so every vote costs a clustered-row fetch. That
crossed the 25s ``max_statement_time`` and hard-500'd ``/current-judges/``
on Minnesota (27.4s measured 2026-08-26; AZ 23.9s and LA 14.7s were one
ingest away from the same cliff) -- and a killed statement also poisons the
pooled connection, so the blast radius was site-wide, not one page.

The span changes only when new opinions arrive, i.e. weekly. So it is
denormalized onto ``Judge`` (a ~900-row table) and refreshed here.

Run after any pass that creates panel votes -- ``resolve_judges``, an
ingest, a merge. Chained into ``cron-ingest.sh`` so the weekly pipeline
keeps it current without anyone remembering.

Cost: one grouped aggregate per state, uncapped, chunked by judge so no
single statement approaches the cull. Seconds per state.

Usage::

    python manage.py backfill_judge_spans
    python manage.py backfill_judge_spans --state LA
    python manage.py backfill_judge_spans --dry-run
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Max, Min

from opinions.models import Judge, PanelVote, State

# Judges per aggregate statement. Small enough that one chunk stays far
# under the statement cap even on the densest state, large enough that a
# full roster is a handful of queries.
CHUNK = 40


class Command(BaseCommand):
    help = "Recompute Judge.first_vote_date / last_vote_date from panel votes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state",
            default=None,
            help="Limit to one state code (e.g. 'LA'). Default: every state.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change; write nothing.",
        )

    def handle(self, *args, state, dry_run, **options):
        # Batch work: lift the web-request statement cap (CLAUDE.md gotcha).
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        states = (
            list(State.objects.filter(code=state.upper()))
            if state else list(State.objects.all().order_by("code"))
        )
        if not states:
            self.stderr.write(f"No such state: {state!r}")
            return

        grand_changed = grand_total = 0
        for st in states:
            t0 = time.time()
            judges = list(Judge.objects.filter(state=st).only(
                "id", "first_vote_date", "last_vote_date"
            ))
            ids = [j.id for j in judges]
            spans: dict[int, tuple] = {}
            for i in range(0, len(ids), CHUNK):
                for row in (
                    PanelVote.objects.filter(judge_id__in=ids[i:i + CHUNK])
                    .values("judge_id")
                    .annotate(
                        first_op=Min("opinion__release_date"),
                        last_op=Max("opinion__release_date"),
                    )
                ):
                    spans[row["judge_id"]] = (row["first_op"], row["last_op"])

            changed = []
            for j in judges:
                first, last = spans.get(j.id, (None, None))
                if (j.first_vote_date, j.last_vote_date) != (first, last):
                    j.first_vote_date, j.last_vote_date = first, last
                    changed.append(j)

            if changed and not dry_run:
                Judge.objects.bulk_update(
                    changed, ["first_vote_date", "last_vote_date"], batch_size=200
                )

            grand_changed += len(changed)
            grand_total += len(judges)
            self.stdout.write(
                f"  [{st.code}] {len(judges):>4} judges, "
                f"{len(spans):>4} with votes, "
                f"{len(changed):>4} updated  ({time.time() - t0:.1f}s)"
                + ("  [dry-run]" if dry_run else "")
            )

        self.stdout.write(self.style.SUCCESS(
            f"\n{grand_changed} of {grand_total} judge span(s) refreshed."
            + ("  (DRY RUN -- nothing saved)" if dry_run else "")
        ))
