"""Per-state ingest freshness monitor.

Run weekly via an NFSN scheduled task (scripts/freshness_check.sh). Exits
non-zero with a loud, actionable message -- which NFSN emails to the site
owner -- when any live state's newest opinion is older than that state's
staleness threshold. Exits 0 (and prints a status table) when all live states
are fresh.

This is the longevity safety net for the per-state scraper model: a scraper or
ingest cron that silently stops looks *identical* to a quiet docket until
someone goes looking. This check makes that failure mode loud instead of
invisible.

Freshness signal = the newest ``release_date`` per state, found with an indexed
``ORDER BY release_date LIMIT 1`` (NOT ``aggregate(Max(...))``, which scans the
whole corpus under a ``court_id__in`` filter -- see the CLAUDE.md gotcha). The
per-state thresholds are deliberately generous: they absorb CourtListener's
~month ingest lag (MN/AZ) and NH Supreme's irregular publishing, so a *healthy*
pipeline never trips them. The goal is catching a weeks-dead pipeline, not
nagging about normal lag.

Caveat (v1): newest-release_date can't perfectly distinguish "pipeline dead"
from "court genuinely quiet." A future refinement is a per-pipeline liveness
beacon (each scraper/cron writes a timestamp when it RUNS, like
.embed_progress), so we can alert on "didn't run" independently of "nothing
new." For now, generous thresholds + the cheap weekly cadence make the
false-alarm rate low and the cost of a false alarm (run the scraper, confirm
the court was quiet) small.
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from opinions.models import Opinion, State


# Max days since a state's newest opinion before we alert. Tuned to sit well
# above each state's normal newest-opinion age (CL lag / scraper cadence /
# court output rate). NH is laxer because the NH Supreme Court publishes
# irregularly and can legitimately be quiet for several weeks. Add a state
# here when it goes live; unlisted live states use DEFAULT_THRESHOLD_DAYS.
STALENESS_THRESHOLD_DAYS = {
    "MN": 45,   # weekly CL cron; CL lag ~month -> newest often ~2-3 wk old
    "AZ": 45,   # weekly CL cron; similar lag profile to MN
    "NH": 60,   # residential scraper + CL; NH Supreme output is irregular
}
DEFAULT_THRESHOLD_DAYS = 45


class Command(BaseCommand):
    help = (
        "Alert (non-zero exit) when any live state's newest opinion is older "
        "than its staleness threshold -- a sign its scraper/ingest cron stalled."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--today",
            help="Override today's date (YYYY-MM-DD) for testing/reproducibility.",
        )

    def handle(self, *args, today=None, **options):
        ref = parse_date(today) if today else date.today()
        if ref is None:
            raise CommandError(f"--today must be ISO YYYY-MM-DD; got {today!r}")

        lines = []
        stale = []
        for state in State.objects.filter(is_live=True).order_by("code"):
            court_ids = list(state.courts.values_list("id", flat=True))
            threshold = STALENESS_THRESHOLD_DAYS.get(state.code, DEFAULT_THRESHOLD_DAYS)
            if not court_ids:
                lines.append(f"  {state.code:<3}  NO COURTS configured")
                continue

            qs = Opinion.objects.filter(court_id__in=court_ids)
            # Indexed walk, not aggregate(Max) -- avoids a corpus scan.
            newest = (
                qs.order_by("-release_date")
                .values_list("release_date", flat=True)
                .first()
            )
            pending = qs.filter(embedding_pending=True).count()

            if newest is None:
                lines.append(f"  {state.code:<3}  NO OPINIONS in corpus")
                stale.append(state.code)
                continue

            age = (ref - newest).days
            flag = "  <-- STALE" if age > threshold else ""
            lines.append(
                f"  {state.code:<3}  newest={newest}  age={age}d  "
                f"threshold={threshold}d  pending_embed={pending}{flag}"
            )
            if age > threshold:
                stale.append(state.code)

        report = f"Ingest freshness @ {ref}\n" + "\n".join(lines)

        if stale:
            # Non-zero exit -> NFSN emails this stderr block to the owner.
            raise CommandError(
                f"STALE ingest for: {', '.join(stale)}\n{report}\n"
                "A scraper or ingest cron may have silently stopped. Check that "
                "state's pipeline (NFSN Scheduled Tasks for CL crons; the "
                "residential scraper for NH/MN-COA) and re-run it."
            )

        self.stdout.write(report)
        self.stdout.write(self.style.SUCCESS("All live states fresh."))
