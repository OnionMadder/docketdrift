"""Move one judge's votes, on given courts from a given date, to another judge.

WHY THIS EXISTS (2026-09-26). Two real people can share a surname, and the
resolver's service-window disambiguation feeds itself: once a "Cole, J."
vote from 2025 lands on Luther F. Cole (a Louisiana justice of the 1980s
and 90s), his window stretches to 2025, so every later "Cole, J." lands on
him too -- while the seated Justice Cade R. Cole, with no votes and no
appointment date, is never a candidate. Measured: the whole seated Louisiana
appellate bench showed ZERO votes while surname twins and surname-only rows
held their 2015+ panels.

The merge commands cannot fix that: both rows are real people with real
votes, so neither row may be deleted. This SPLITS a row instead -- only the
votes on the named courts, inside the given date range, and only where the
opinion's own text supports the move.

EVIDENCE GATE (per vote, not per row):
  * the target's surname must appear in the opinion text, and
  * the SOURCE judge's distinguishing first name must NOT appear next to
    that surname (e.g. "Luther Cole", "Virginia C. Kelly") -- if the text
    names the source judge in full, the vote stays with the source.
Votes that fail the gate are reported and left alone.

Collisions (target already has a vote on the opinion) keep the stronger
vote type, the same rule the merge commands use.

Dry run by default; --apply to write. Afterwards both judges' denormalized
first/last vote dates are recomputed so /current-judges/ reflects the move.

Usage::

    python manage.py reassign_judge_votes --from 123 --to 456 \\
        --courts 7 --on-or-after 2023-01-01
    python manage.py reassign_judge_votes ... --apply
"""
from __future__ import annotations

import datetime
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Max, Min

from opinions.judge_merge import VOTE_RANK, surname
from opinions.models import Judge, Opinion, PanelVote

CHUNK = 500


def _first_name(full_name: str) -> str:
    """First given-name token ("Luther F. Cole" -> "Luther"), '' if surname-only."""
    parts = full_name.replace(",", " ").split()
    if len(parts) < 2:
        return ""
    first = parts[0].strip(".")
    return "" if len(first) <= 1 else first


class Command(BaseCommand):
    help = "Split a judge row: move votes on given courts/dates to another judge, gated on text evidence."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="src", type=int, required=True, help="Source Judge pk.")
        parser.add_argument("--to", dest="dst", type=int, required=True, help="Target Judge pk.")
        parser.add_argument("--courts", required=True,
                            help="Comma-separated Court pks the move applies to.")
        parser.add_argument("--on-or-after", required=True, help="YYYY-MM-DD (inclusive).")
        parser.add_argument("--before", default=None, help="YYYY-MM-DD (exclusive), optional.")
        parser.add_argument("--samples", type=int, default=4,
                            help="Evidence snippets to print (default 4).")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, src, dst, courts, on_or_after, before, samples, apply, **opts):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        try:
            source = Judge.objects.select_related("court").get(pk=src)
            target = Judge.objects.select_related("court").get(pk=dst)
        except Judge.DoesNotExist as e:
            raise CommandError(str(e))
        if source.pk == target.pk:
            raise CommandError("--from and --to are the same judge.")
        if source.state_id != target.state_id:
            raise CommandError("Judges are in different states (%s vs %s)." % (source.state_id, target.state_id))
        sur = surname(target.full_name)
        if surname(source.full_name).lower() != sur.lower():
            raise CommandError("Surnames differ (%r vs %r); this tool only splits surname twins."
                               % (source.full_name, target.full_name))
        court_ids = [int(c) for c in courts.split(",") if c.strip()]
        start = datetime.date.fromisoformat(on_or_after)
        end = datetime.date.fromisoformat(before) if before else None

        self.stdout.write("source: #%d %s (%s)   ->   target: #%d %s (%s)" % (
            source.pk, source.full_name, source.court, target.pk, target.full_name, target.court))
        self.stdout.write("scope:  courts %s, %s .. %s" % (court_ids, start, end or "now"))

        # Source's votes -> the opinions in scope. Filter the small side first:
        # a judge has at most a few thousand votes; the opinions table is 2.75GB.
        votes = dict(PanelVote.objects.filter(judge=source).values_list("opinion_id", "vote_type"))
        in_scope = []
        ids = list(votes)
        for i in range(0, len(ids), CHUNK):
            qs = Opinion.objects.filter(id__in=ids[i:i + CHUNK], court_id__in=court_ids,
                                        release_date__gte=start)
            if end:
                qs = qs.filter(release_date__lt=end)
            in_scope += list(qs.values_list("id", "case_number", "release_date"))
        self.stdout.write("source votes: %d total, %d in scope" % (len(votes), len(in_scope)))
        if not in_scope:
            return

        sur_rx = re.compile(r"\b%s\b" % re.escape(sur), re.I)
        first = _first_name(source.full_name)
        tfirst = _first_name(target.full_name)
        # Source named in full: "Luther F. Cole", "Luther Cole", "Virginia C. Kelly".
        src_rx = (re.compile(r"\b%s\b(?:\s+[A-Z]\.?)?\s+%s\b" % (re.escape(first), re.escape(sur)), re.I)
                  if first and first.lower() != tfirst.lower() else None)

        move, refused = [], []
        for i in range(0, len(in_scope), 200):
            batch = in_scope[i:i + 200]
            texts = dict(Opinion.objects.filter(id__in=[b[0] for b in batch]).values_list("id", "raw_text"))
            for oid, cn, rd in batch:
                t = texts.get(oid) or ""
                if not sur_rx.search(t):
                    refused.append((oid, cn, rd, "surname absent from text"))
                elif src_rx and src_rx.search(t):
                    refused.append((oid, cn, rd, "text names %s in full" % source.full_name))
                else:
                    move.append((oid, cn, rd, t))

        by_year = {}
        for _, _, rd, _ in move:
            by_year[rd.year] = by_year.get(rd.year, 0) + 1
        self.stdout.write("passes evidence gate: %d   refused: %d" % (len(move), len(refused)))
        self.stdout.write("  by year: " + ", ".join("%d:%d" % kv for kv in sorted(by_year.items())))
        for oid, cn, rd, why in refused[:10]:
            self.stdout.write("  REFUSED %s %s (%s)" % (cn, rd, why))
        for oid, cn, rd, t in move[:samples]:
            m = sur_rx.search(t)
            ctx = re.sub(r"\s+", " ", t[max(0, m.start() - 90):m.end() + 40])
            self.stdout.write("  sample %s %s: ...%s..." % (cn, rd, ctx))

        if not apply:
            self.stdout.write("DRY RUN -- nothing written. Re-run with --apply.")
            return

        existing = dict(PanelVote.objects.filter(judge=target, opinion_id__in=[m[0] for m in move])
                        .values_list("opinion_id", "vote_type"))
        moved = collided = 0
        with transaction.atomic():
            for oid, _, _, _ in move:
                if oid in existing:
                    # Keep the stronger of the two votes on the target; drop the source's.
                    if VOTE_RANK.get(votes[oid], 0) > VOTE_RANK.get(existing[oid], 0):
                        PanelVote.objects.filter(judge=target, opinion_id=oid).update(vote_type=votes[oid])
                    PanelVote.objects.filter(judge=source, opinion_id=oid).delete()
                    collided += 1
                else:
                    PanelVote.objects.filter(judge=source, opinion_id=oid).update(judge=target)
                    moved += 1

        for j in (source, target):
            agg = PanelVote.objects.filter(judge=j).aggregate(
                first=Min("opinion__release_date"), last=Max("opinion__release_date"))
            Judge.objects.filter(pk=j.pk).update(first_vote_date=agg["first"], last_vote_date=agg["last"])
            self.stdout.write("  %s now spans %s .. %s" % (j.full_name, agg["first"], agg["last"]))
        self.stdout.write("APPLIED: %d moved, %d collisions resolved." % (moved, collided))
