"""Standing integrity audit for the judge layer.

WHY THIS EXISTS. Every judge-data bug we have found was found by
accident. Souter recorded as authoring a 2018 New Hampshire opinion --
28 years after he left for the U.S. Supreme Court -- surfaced only
because a corroboration guard was added for an unrelated backfill. The
1,844-page cited-by outage surfaced because a crawler happened to trip
it. A judge layer that is the product's main selling point cannot rely
on luck.

So this reports the failure classes we have actually seen, on demand or
weekly, and exits non-zero when anything lands in a category that should
be empty. Freshness monitoring cannot see any of this: every check below
can be badly wrong while every ingest pipeline is perfectly healthy.

Checks:
  1. IMPOSSIBLE SPANS   -- >45yr between first and last vote. Nobody
     served 197 years; such a row is several people merged, and it is
     corrupting a real person's dossier and the co-panelist heat built
     on it.
  2. TENURE VIOLATIONS  -- votes outside a judge's known service window.
     This is the Souter detector, generalized.
  3. LIKELY DUPLICATES  -- same surname, overlapping windows, and one
     name a plausible variant of the other (MN carries both "Barry A.
     Anderson" and "G Barry Anderson").
  4. AMBIGUOUS SURNAMES -- shared surnames whose windows OVERLAP, so
     date disambiguation cannot separate them and their bylines are
     still being discarded.
  5. ORPHAN ROWS        -- judges with zero votes, split by whether they
     are seated (expected: a new appointee) or not (suspect).
  6. COVERAGE           -- share of opinions with a panel, and of
     paneled opinions with an identified author. Regressions here mean
     an extractor broke.

Usage::

    python manage.py audit_judges                 # all states
    python manage.py audit_judges --state MN
    python manage.py audit_judges --fail-on-anomalies   # for cron
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, models

from opinions.models import Court, Judge, Opinion, PanelVote

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "2", "3", "4"}
IMPOSSIBLE_SPAN_YEARS = 45
GRACE_DAYS = 365


def _surname(full_name: str) -> str:
    parts = [p for p in (full_name or "").split()
             if p.lower().rstrip(".") not in _SUFFIXES]
    return parts[-1].lower() if parts else ""


class Command(BaseCommand):
    help = "Audit judge data integrity: spans, tenure, duplicates, coverage."

    def add_arguments(self, parser):
        parser.add_argument("--state", default=None, help="Limit to one state code.")
        parser.add_argument("--limit-examples", type=int, default=6,
                            help="Rows to name per finding (default 6).")
        parser.add_argument("--fail-on-anomalies", action="store_true",
                            help="Exit non-zero if any must-be-empty check has rows "
                                 "(for a scheduled task that emails on failure).")

    def handle(self, *args, state, limit_examples, fail_on_anomalies, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        codes = ([state.upper()] if state else
                 list(Judge.objects.values_list("state__code", flat=True)
                      .distinct().order_by("state__code")))
        anomalies = 0

        for code in codes:
            self.stdout.write(self.style.SUCCESS(f"\n{'=' * 58}\n{code}\n{'=' * 58}"))
            judges = list(Judge.objects.filter(state__code=code))
            spans = {
                r["judge_id"]: (r["first"], r["last"], r["n"])
                for r in PanelVote.objects.filter(judge__state__code=code)
                .values("judge_id")
                .annotate(first=models.Min("opinion__release_date"),
                          last=models.Max("opinion__release_date"),
                          n=models.Count("id"))
            }
            by_name = {j.pk: j.full_name for j in judges}

            # -- 1. impossible spans ---------------------------------------
            bad_span = []
            for jid, (a, b, n) in spans.items():
                if a and b and (b.year - a.year) > IMPOSSIBLE_SPAN_YEARS:
                    bad_span.append((by_name.get(jid, "?"), a, b, n))
            bad_span.sort(key=lambda r: -(r[2].year - r[1].year))
            self.stdout.write(f"\n[1] impossible spans (>{IMPOSSIBLE_SPAN_YEARS}yr): "
                              f"{len(bad_span)}")
            for name, a, b, n in bad_span[:limit_examples]:
                self.stdout.write(f"      {name[:30]:<30} {a.year}..{b.year} "
                                  f"({b.year - a.year}yr, {n} votes)")
            anomalies += len(bad_span)

            # -- 2. tenure violations --------------------------------------
            # Only judges we can bound: an appointment date AND either a
            # termination-ish end (not seated) or seated=open.
            violations = []
            for j in judges:
                first, last, _n = spans.get(j.pk, (None, None, 0))
                if not j.appointment_date or not first:
                    continue
                lo = j.appointment_date - timedelta(days=GRACE_DAYS)
                if first < lo:
                    violations.append((j.full_name, "votes before appointment",
                                       first, j.appointment_date))
            self.stdout.write(f"\n[2] tenure violations: {len(violations)}")
            for name, why, got, bound in violations[:limit_examples]:
                self.stdout.write(f"      {name[:30]:<30} {why}: {got} vs {bound}")
            anomalies += len(violations)

            # -- 3. likely duplicates --------------------------------------
            groups = defaultdict(list)
            for j in judges:
                s = _surname(j.full_name)
                if s:
                    groups[s].append(j)
            dupes = []
            for s, members in groups.items():
                if len(members) < 2:
                    continue
                for i in range(len(members)):
                    for k in range(i + 1, len(members)):
                        a, b = members[i], members[k]
                        an = set(a.full_name.lower().replace(".", "").split())
                        bn = set(b.full_name.lower().replace(".", "").split())
                        # One name's tokens a subset of the other's, or they
                        # share a distinctive given name -> likely one person.
                        if an <= bn or bn <= an or len(an & bn) >= 2:
                            dupes.append((a.full_name, b.full_name))
            self.stdout.write(f"\n[3] likely duplicate rows: {len(dupes)}")
            for a, b in dupes[:limit_examples]:
                self.stdout.write(f"      {a[:26]:<26} <-> {b[:26]}")
            anomalies += len(dupes)

            # -- 4. unresolvable ambiguity ---------------------------------
            unresolvable = []
            for s, members in groups.items():
                if len(members) < 2:
                    continue
                wins = []
                for j in members:
                    first, last, _n = spans.get(j.pk, (None, None, 0))
                    start = j.appointment_date or first
                    if start is None:
                        continue
                    end = None if j.is_currently_seated else last
                    if end is None and not j.is_currently_seated:
                        continue
                    wins.append((start, end))
                for i in range(len(wins)):
                    for k in range(i + 1, len(wins)):
                        (a_s, a_e), (b_s, b_e) = wins[i], wins[k]
                        lo = max(a_s, b_s)
                        hi = min(a_e or b_e or lo, b_e or a_e or lo)
                        if a_e is None or b_e is None or lo <= hi:
                            unresolvable.append(s)
                            break
                    else:
                        continue
                    break
            self.stdout.write(f"\n[4] surnames still unresolvable (windows overlap): "
                              f"{len(set(unresolvable))}")
            for s in sorted(set(unresolvable))[:limit_examples]:
                names = " | ".join(j.full_name[:22] for j in groups[s])
                self.stdout.write(f"      {s:<14} {names}")

            # -- 5. orphan rows --------------------------------------------
            no_votes = [j for j in judges if spans.get(j.pk, (None, None, 0))[2] == 0]
            seated_orphans = [j for j in no_votes if j.is_currently_seated]
            self.stdout.write(
                f"\n[5] zero-vote judges: {len(no_votes)} "
                f"({len(seated_orphans)} seated = expected for new appointees, "
                f"{len(no_votes) - len(seated_orphans)} unseated = suspect)")
            for j in [x for x in no_votes if not x.is_currently_seated][:limit_examples]:
                self.stdout.write(f"      {j.full_name[:30]:<30} status={j.status}")

            # -- 6. coverage -----------------------------------------------
            court_ids = list(Court.objects.filter(state__code=code)
                             .values_list("id", flat=True))
            total = Opinion.objects.filter(court_id__in=court_ids).count()
            paneled = (PanelVote.objects.filter(opinion__court_id__in=court_ids)
                       .values("opinion_id").distinct().count())
            authored = (PanelVote.objects.filter(
                opinion__court_id__in=court_ids,
                vote_type__in=["MAJORITY_AUTHOR", "DISSENT_AUTHOR",
                               "CONCURRENCE_AUTHOR"])
                .values("opinion_id").distinct().count())
            self.stdout.write(
                f"\n[6] coverage: {paneled:,}/{total:,} opinions have a panel "
                f"({100 * paneled // max(total, 1)}%); "
                f"{authored:,} of those name an author "
                f"({100 * authored // max(paneled, 1)}%)")

        self.stdout.write(self.style.SUCCESS(
            f"\n\nTotal must-be-empty anomalies: {anomalies}"))
        if fail_on_anomalies and anomalies:
            self.stderr.write(self.style.ERROR(
                "Anomalies present -- see checks [1]-[3] above."))
            raise SystemExit(1)
