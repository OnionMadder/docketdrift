"""Set status=RETIRED for former NH justices, sourced from CourtListener.

WHY CL AND NOT INFERENCE. The 24 remaining NH judges sat at
status=UNKNOWN. The tempting shortcut is "no votes in N years -> RETIRED",
but that infers a fact about a person from the absence of data, and the
absence could equally mean our corpus is thin for that era. CL's people
DB carries an actual ``date_termination`` on each judicial position, so
we can cite a source instead of guessing.

CORROBORATION IS REQUIRED, NOT ASSUMED. CL's historical position data is
uneven -- some rows carry placeholder dates where start == termination
== the same January 1st (Charles G. Douglas III's judicial position reads
1976-01-01..1976-01-01, which is not a real tenure). So a termination
date is only accepted when it is consistent with the judge's own voting
record in OUR corpus: the position must not end before their last vote.
A CL row that contradicts our data is reported and skipped, never
written.

WHAT THIS DELIBERATELY WILL NOT DO: set ``role``. Every position CL
returns for these judges has position_type 'jud' -- generic Judge, with
no chief/associate distinction. Several of these people were Chief
Justice of New Hampshire and several were not, and nothing in this
source separates them. Guessing would put a fabricated title on a named
judge's public page. Roles stay UNKNOWN until sourced from the court's
own list of Chief Justices (MacDonald is styled "the 37th Chief
Justice", so an authoritative ordered list exists to be found).

Idempotent. Dry-run by default; --apply to commit.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Max

from opinions.courtlistener import CourtListenerClient, CourtListenerError
from opinions.models import Judge, PanelVote

# CL position_type prefixes that denote a judicial seat.
_JUDICIAL_PREFIXES = ("jud", "jus", "c-j", "ass", "ret")


class Command(BaseCommand):
    help = "Set NH judge status=RETIRED from CourtListener position terminations."

    def add_arguments(self, parser):
        parser.add_argument("--state", default="NH", help="State code (default NH).")
        parser.add_argument("--apply", action="store_true",
                            help="Commit. Omit for a dry-run preview.")
        parser.add_argument("--limit", type=int, default=0,
                            help="Process at most N judges (smoke test).")

    def handle(self, *args, state, apply, limit, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        token = getattr(settings, "COURTLISTENER_TOKEN", "") or ""
        if not token:
            raise CommandError("COURTLISTENER_TOKEN is not set.")
        client = CourtListenerClient(token=token)

        qs = (Judge.objects
              .filter(state__code=state.upper(), status=Judge.Status.UNKNOWN)
              .exclude(courtlistener_id="")
              .exclude(courtlistener_id__isnull=True)
              .order_by("full_name"))
        if limit:
            qs = qs[:limit]
        judges = list(qs)

        mode = "APPLY" if apply else "DRY-RUN (nothing written; pass --apply)"
        self.stdout.write(self.style.SUCCESS(
            f"backfill_nh_judge_status — {mode}\n  {len(judges)} UNKNOWN judge(s) with a CL person id\n"))

        retired = skipped_conflict = skipped_nodata = errored = 0

        for j in judges:
            last_vote = (PanelVote.objects.filter(judge=j)
                         .aggregate(m=Max("opinion__release_date"))["m"])
            try:
                page = client._get("positions/",
                                   params={"person": j.courtlistener_id, "page_size": 20})
            except CourtListenerError as exc:
                self.stdout.write(self.style.WARNING(
                    f"  {j.full_name:<30} CL error: {str(exc)[:60]}"))
                errored += 1
                continue

            # Judicial positions with a termination date.
            terms = []
            for p in (page.get("results") or []):
                ptype = (p.get("position_type") or "")
                if not ptype.startswith(_JUDICIAL_PREFIXES):
                    continue
                end = p.get("date_termination")
                if end:
                    terms.append(end)

            if not terms:
                self.stdout.write(
                    f"  {j.full_name:<30} no terminated judicial position in CL -- left UNKNOWN")
                skipped_nodata += 1
                continue

            latest_end = max(terms)

            # Corroboration at YEAR granularity. CL stores many historical
            # terminations as a January-1 placeholder for the year, so a
            # day-level comparison flags "CL says 1986-01-01, we have a vote
            # 1986-05-08" as a contradiction when it is really the same
            # retirement recorded to different precision -- 7 of 9 initial
            # conflicts were exactly that. Comparing years keeps the guard
            # pointed at genuine disagreements: a CL termination YEARS
            # before our last vote means the date is wrong, the seat is a
            # different one, or the votes are misattributed.
            end_year = int(latest_end[:4])
            vote_year = last_vote.year if last_vote else None
            if vote_year and end_year < vote_year:
                self.stdout.write(self.style.WARNING(
                    f"  {j.full_name:<30} CONFLICT: CL ends {latest_end} but we have "
                    f"a vote {last_vote} -- skipped"))
                skipped_conflict += 1
                continue

            self.stdout.write(
                f"  {j.full_name:<30} RETIRED (CL termination {latest_end}"
                + (f", last vote {last_vote}" if last_vote else "") + ")")
            if apply:
                j.status = Judge.Status.RETIRED
                j.is_currently_seated = False
                j.save(update_fields=["status", "is_currently_seated"])
            retired += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Applied' if apply else 'Would set'} RETIRED: {retired}"
            f"\n  left UNKNOWN (no CL termination): {skipped_nodata}"
            f"\n  skipped (CL contradicts our votes): {skipped_conflict}"
            f"\n  CL errors: {errored}"))
        self.stdout.write("  role: untouched by design -- CL cannot distinguish "
                          "Chief from Associate (see module docstring).")
        if not apply:
            self.stdout.write("\nRe-run with --apply to commit.")
