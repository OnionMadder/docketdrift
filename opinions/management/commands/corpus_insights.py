"""Read-only analytics over the corpus itself -- the stuff hiding in the DB.

Reversal/affirmance mix, caseload trend, most-cited opinions (the within-corpus
citation graph), busiest judges, and the most-litigated statutes. Zero privacy
cost -- it's all public court records, aggregated. This is a batch report, so it
lifts the 25s web-request statement cap for its heavier GROUP BYs.

Usage:
    python manage.py corpus_insights [--state CODE] [--years N]
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count
from django.db.models.functions import ExtractYear

from opinions.models import Opinion, OpinionCitation, PanelVote, State, StatuteCitation


class Command(BaseCommand):
    help = "Read-only corpus analytics: dispositions, caseload trend, most-cited, hot statutes."

    def add_arguments(self, parser):
        parser.add_argument("--state", default=None, help="USPS code; default = all live states.")
        parser.add_argument("--years", type=int, default=12, help="Caseload-trend window.")

    def handle(self, *args, state, years, **opts):
        # Batch report, not a web request: let the GROUP BYs run past the 25s cap.
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        if state:
            states = list(State.objects.filter(code=state.upper()))
        else:
            states = list(State.objects.filter(is_live=True).order_by("name"))

        w = self.stdout.write

        for st in states:
            court_ids = list(st.courts.values_list("id", flat=True))
            judge_ids = list(st.judges.values_list("id", flat=True))
            ops = Opinion.objects.filter(court_id__in=court_ids)

            total = ops.values("pk").count()
            first = ops.order_by("release_date").values_list("release_date", flat=True).first()
            last = ops.order_by("-release_date").values_list("release_date", flat=True).first()

            w("=" * 66)
            w(f"{st.name} ({st.code}) -- {total:,} opinions, {first} to {last}")
            w("=" * 66)

            # Disposition mix -- affirm/reverse/etc. as they stand in the record.
            w("\nDisposition mix:")
            disp = list(
                ops.exclude(disposition_bucket="")
                .values("disposition_bucket")
                .annotate(n=Count("id"))
                .order_by("-n")
            )
            dtot = sum(d["n"] for d in disp) or 1
            for d in disp:
                w(f"  {d['n']:>7,}  {100 * d['n'] / dtot:4.0f}%  {d['disposition_bucket']}")

            # Caseload per year.
            cutoff = date(date.today().year - years, 1, 1)
            w(f"\nCaseload, last {years} years:")
            yr = list(
                ops.filter(release_date__gte=cutoff)
                .annotate(y=ExtractYear("release_date"))
                .values("y")
                .annotate(n=Count("id"))
                .order_by("y")
            )
            mx = max((r["n"] for r in yr), default=1)
            for r in yr:
                bar = "#" * int(40 * r["n"] / mx) if mx else ""
                w(f"  {r['y']}  {r['n']:>6,}  {bar}")

            # Busiest judges (by panel seats).
            w("\nMost panel seats (judges):")
            pj = list(
                PanelVote.objects.filter(judge_id__in=judge_ids)
                .values("judge_id")
                .annotate(n=Count("id"))
                .order_by("-n")[:8]
            )
            jmap = {j.id: j.full_name for j in st.judges.all()}
            for r in pj:
                w(f"  {r['n']:>7,}  {jmap.get(r['judge_id'], r['judge_id'])}")

            # Most-cited opinions (within-corpus citation graph; NH in practice).
            cited = list(
                OpinionCitation.objects.filter(
                    cited_opinion__isnull=False, cited_opinion__court_id__in=court_ids
                )
                .values("cited_opinion")
                .annotate(n=Count("id"))
                .order_by("-n")[:10]
            )
            if cited:
                w("\nMost-cited opinions (within-corpus citation graph):")
                omap = {
                    o.id: o
                    for o in Opinion.objects.filter(id__in=[c["cited_opinion"] for c in cited])
                    .select_related("court")
                    .defer("raw_text", "html_content")
                }
                for c in cited:
                    o = omap.get(c["cited_opinion"])
                    if o:
                        label = o.reporter_cite or o.case_number
                        w(f"  {c['n']:>4}x  {label}  {(o.title or '')[:58]}")
            w("")

        # Hot statutes -- done globally: the statute strings self-label by state
        # (Minn. Stat. / A.R.S. / RSA), so this avoids joining the 2.75GB
        # opinions table to attribute per-state.
        w("=" * 66)
        w("Hot statutes (most-cited across all states):")
        w("=" * 66)
        hot = list(
            StatuteCitation.objects.exclude(reference_display="")
            .values("reference_display")
            .annotate(n=Count("id"))
            .order_by("-n")[:20]
        )
        for h in hot:
            w(f"  {h['n']:>7,}  {h['reference_display']}")
