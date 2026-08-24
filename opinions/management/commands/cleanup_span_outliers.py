"""Remove temporally impossible PanelVotes caused by citation leaks.

THE SYMPTOM. audit_judges reports judges whose first and last vote are
decades too far apart to be one career: Walter F. Rogosheske spanning
1968-2024, Luther F. Cole 1858-2026, LA's "Martin" 1811-2008.

THE TWO CAUSES, which need opposite treatment. Reading the per-decade
vote histograms separates them cleanly:

  (a) CITATION LEAK -- a real judge with a tight tenure plus a handful
      of strays in a distant era. Rogosheske is 1960s:23, 1970s:91,
      1980s:2 ... and 2020s:1. That lone modern vote is a later opinion
      CITING him, read as a byline. Same bug as NH's Souter (recorded
      as authoring a 2018 opinion 28 years after leaving for SCOTUS).

  (b) TWO PEOPLE, ONE ROW -- two substantial clusters far apart. LA's
      "Martin" is 1810s-1820s (3 votes) AND 1990s-2000s (5 votes);
      Louisiana had a Martin on the bench in both eras. Deleting either
      side would erase a real judge's record.

This command only fixes (a), and it does not decide which case it is
from the histogram. The histogram merely says WHERE to look; the actual
gate is evidence from the opinion text: a vote is deleted ONLY if the
judge's surname appears exclusively INSIDE PARENTHESES in that opinion
-- i.e. purely as a citation, never as a bare signoff. Anything else is
reported for review, so case (b) survives untouched and is surfaced for
a human to split.

Dry-run by default; --apply commits.
"""
from __future__ import annotations

import re
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection, models

from opinions.models import Judge, Opinion, PanelVote

SPAN_YEARS = 45          # what counts as impossible
CORE_PAD_YEARS = 15      # slack around the judge's core era
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "2", "3", "4"}


def _surname(full_name: str) -> str:
    parts = [p for p in (full_name or "").split()
             if p.lower().rstrip(".") not in _SUFFIXES]
    return parts[-1] if parts else ""


def _citation_only(raw: str, surname: str) -> bool:
    """True if every mention of ``surname`` sits inside an open paren.

    The discriminator CLAUDE.md documents: "SURNAME, J., concurring"
    inside parentheses is a citation to another court's opinion; a bare
    one is this court's signoff.
    """
    hits = list(re.finditer(re.escape(surname), raw or ""))
    if not hits:
        return False                       # no mention at all -> not our evidence
    for m in hits:
        pre = (raw or "")[max(0, m.start() - 150):m.start()]
        if pre.rfind("(") <= pre.rfind(")"):
            return False                   # a bare mention exists
    return True


class Command(BaseCommand):
    help = "Delete citation-leak votes that make a judge's service span impossible."

    def add_arguments(self, parser):
        parser.add_argument("--state", default=None, help="Limit to one state code.")
        parser.add_argument("--apply", action="store_true", help="Commit.")
        parser.add_argument("--max-judges", type=int, default=0,
                            help="Process at most N judges (smoke test).")

    def handle(self, *args, state, apply, max_judges, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        codes = ([state.upper()] if state else
                 list(Judge.objects.values_list("state__code", flat=True)
                      .distinct().order_by("state__code")))

        mode = "APPLY" if apply else "DRY-RUN (nothing deleted; pass --apply)"
        self.stdout.write(self.style.SUCCESS(f"cleanup_span_outliers — {mode}\n"))
        total_del = total_keep = 0

        for code in codes:
            spans = {r["judge_id"]: (r["first"], r["last"]) for r in
                     PanelVote.objects.filter(judge__state__code=code)
                     .values("judge_id")
                     .annotate(first=models.Min("opinion__release_date"),
                               last=models.Max("opinion__release_date"))}
            bad_ids = [jid for jid, (a, b) in spans.items()
                       if a and b and (b.year - a.year) > SPAN_YEARS]
            if not bad_ids:
                continue
            if max_judges:
                bad_ids = bad_ids[:max_judges]
            self.stdout.write(self.style.SUCCESS(
                f"\n===== {code}: {len(bad_ids)} impossible-span judge(s) ====="))

            for jid in bad_ids:
                judge = Judge.objects.get(pk=jid)
                sur = _surname(judge.full_name)
                votes = list(PanelVote.objects.filter(judge=judge)
                             .values("id", "opinion_id", "vote_type",
                                     "opinion__release_date"))
                years = [v["opinion__release_date"].year for v in votes
                         if v["opinion__release_date"]]
                if not years:
                    continue

                # Core era = the decade holding the most votes, widened to
                # the contiguous decades around it that still hold votes.
                hist = Counter((y // 10) * 10 for y in years)
                peak = max(hist, key=lambda d: hist[d])
                lo = hi = peak
                while hist.get(lo - 10):
                    lo -= 10
                while hist.get(hi + 10):
                    hi += 10
                lo -= CORE_PAD_YEARS
                hi += 10 + CORE_PAD_YEARS

                outliers = [v for v in votes
                            if v["opinion__release_date"]
                            and not (lo <= v["opinion__release_date"].year <= hi)]
                if not outliers:
                    continue

                self.stdout.write(
                    f"\n  {judge.full_name[:30]:<30} core {lo}-{hi}, "
                    f"{len(outliers)} outlier vote(s)")

                for v in sorted(outliers, key=lambda x: x["opinion__release_date"]):
                    op = (Opinion.objects.filter(pk=v["opinion_id"])
                          .only("id", "case_number", "raw_text").first())
                    if op is None:
                        continue
                    if _citation_only(op.raw_text or "", sur):
                        self.stdout.write(
                            f"      DELETE {v['opinion__release_date']} "
                            f"{(op.case_number or '')[:16]:<16} {v['vote_type']}"
                            f"  (citation-only)")
                        if apply:
                            PanelVote.objects.filter(pk=v["id"]).delete()
                        total_del += 1
                    else:
                        self.stdout.write(
                            f"      keep   {v['opinion__release_date']} "
                            f"{(op.case_number or '')[:16]:<16} {v['vote_type']}"
                            f"  (bare mention -- REVIEW: may be a second judge)")
                        total_keep += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n\n{'Deleted' if apply else 'Would delete'}: {total_del} citation-leak vote(s)."
            f"\nKept for review: {total_keep} (bare mentions -- likely a real "
            f"second judge sharing the surname; needs a manual split)."))
        if not apply:
            self.stdout.write("Re-run with --apply to commit.")
