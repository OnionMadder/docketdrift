"""One-shot cull of LA junk-judge rows minted by the pre-2026-08-25 parser.

The LA panel extraction had four leak classes (fixed the same day in
``parsing/la.py`` + ``resolve_judges`` -- see commit 869a829): the
Chief Judge marker ("C", 961 votes), party words from an uncut
panel-composed sentence ("Defendant" 436, plus actual party surnames
like "Bolton"), role riders ("Tempore" from "Pro Tempore"), and the
per-curiam author string ("Curiam", 254 MAJORITY_AUTHOR votes). This
command deletes the STALE rows those leaks left behind.

Discipline (mirrors ``cleanup_az_judges``):

- Explicit pk candidate list -- nothing is pattern-matched at run time.
- EVIDENCE GATE per judge: up to ``SAMPLE_CAP`` of its vote opinions are
  re-extracted with the FIXED pipeline (state parser + generic byline
  extractor, the same union ``resolve_judges`` uses). If either path
  still emits the judge's surname on any sampled opinion, the judge is
  REFUSED and reported -- a real judge can never be culled by this
  command, even if it lands on the candidate list by mistake.
- Editorial-metadata guard: a row with a bio/photo/roster status is
  refused outright (it has been human-touched).
- Dry-run by default; ``--apply`` to commit. Idempotent (already-deleted
  pks are reported and skipped).

Deliberately NOT candidates: ``St.pierre`` (1843) and ``L.cannella``
(1768) -- OCR-mangled forms of REAL judges (St. Pierre / Cannella);
their single votes are merge material, not junk. Deleting a real vote
is worse than keeping an uncertain one.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from opinions.models import Judge, PanelVote

# pk -> recorded full_name at audit time (2026-08-25). The name is a
# sanity check: if the row's current name differs, the pk has been
# reused/edited and the judge is refused.
CANDIDATES = {
    713: "C",            # Chief Judge marker from "WHIPPLE, C. J., ..."
    691: "Defendant",    # party word, uncut panel-composed sentence
    674: "Curiam",       # per-curiam author string
    677: "Plaintiff",    # party word
    1012: "Tempore",     # "Pro Tempore" role rider
    1182: "Cj",          # unspaced Chief Judge marker
    1206: "J",           # bare marker
    779: "Appellant",    # party word
    830: "Co",           # caption fragment ("... & Co.")
    1806: "G",
    1071: "F",
    1345: "And",
    1814: "D",
    737: "D.a",          # "D.A." (district attorney)
    874: "Mr",
    1067: "L.l.c",       # caption fragment
    1083: "Emoneyport.com",  # caption fragment (a party's domain name)
    1121: "Le",          # sentence fragment, verified not a judge byline
    1266: "Pa",
    1312: "Si",
    1355: "S.j",
    1388: "C.p",
    1472: "Po",
    1492: "Judge",
    1589: "Vu",
    1700: "Pe",
    1759: "Th",
    1847: "On",
    1856: "L",
    1925: "Appellee",    # party word
    1938: "Ph",
    692: "Bolton",       # the DEFENDANT in 02-KA-1034 (verified party leak)
}

SAMPLE_CAP = 8


class Command(BaseCommand):
    help = "Cull LA junk-judge rows left by the pre-2026-08-25 parser leaks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually delete. Default is dry-run report only.",
        )

    def handle(self, *args, apply, **options):
        from opinions.parsing import parse as parse_opinion
        from opinions.management.commands.resolve_judges import (
            _extract_generic_byline,
            _last_name,
            _valid_surname,
        )

        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        culled = refused = missing = 0
        votes_deleted = 0

        for pk, recorded_name in CANDIDATES.items():
            judge = Judge.objects.filter(pk=pk).first()
            if judge is None:
                self.stdout.write(f"  [gone]    pk={pk} ({recorded_name!r}) "
                                  "already deleted; skipping.")
                missing += 1
                continue
            if judge.full_name != recorded_name:
                self.stdout.write(self.style.WARNING(
                    f"  [REFUSE]  pk={pk}: name is {judge.full_name!r}, "
                    f"expected {recorded_name!r} -- row changed since audit."
                ))
                refused += 1
                continue
            if judge.court_id is not None or judge.bio_summary or \
                    judge.photo_url or judge.status != "UNKNOWN":
                self.stdout.write(self.style.WARNING(
                    f"  [REFUSE]  pk={pk} ({recorded_name!r}): carries "
                    "editorial metadata / court seat -- human-touched."
                ))
                refused += 1
                continue

            surname = _last_name(judge.full_name).lower()
            vote_qs = PanelVote.objects.filter(judge=judge)
            vote_count = vote_qs.count()

            # Evidence gate: re-extract a sample of this judge's own vote
            # opinions with the FIXED pipeline. Any hit = real judge.
            sampled = 0
            still_extracted_on = None
            for vote in vote_qs.select_related("opinion")[:SAMPLE_CAP]:
                text = vote.opinion.raw_text
                if not text:
                    continue
                sampled += 1
                names = set()
                result = parse_opinion("LA", text)
                if result is not None:
                    if result.author:
                        names.add(_last_name(result.author).lower())
                    for p in result.panel:
                        names.add(_last_name(p).lower())
                generic = _extract_generic_byline(text)
                if generic.author_last:
                    names.add(generic.author_last)
                names.update(generic.panel_last)
                names.update(generic.dissenter_last)
                names.update(generic.concurrer_last)
                # Mirror the fixed resolve_judges hybrid path: a name the
                # resolver would REJECT via _valid_surname can never
                # receive a vote again, so it does not count as evidence.
                # (Without this, the raw parser output for e.g. a
                # per-curiam author string refuses its own cull.)
                names = {n for n in names if _valid_surname(n)}
                if surname in names:
                    still_extracted_on = vote.opinion_id
                    break

            if still_extracted_on is not None:
                self.stdout.write(self.style.WARNING(
                    f"  [REFUSE]  pk={pk} ({recorded_name!r}): fixed "
                    f"pipeline STILL extracts {surname!r} on opinion "
                    f"{still_extracted_on} -- not a stale leak."
                ))
                refused += 1
                continue
            if vote_count and sampled == 0:
                self.stdout.write(self.style.WARNING(
                    f"  [REFUSE]  pk={pk} ({recorded_name!r}): no sampled "
                    "opinion had text to verify against -- won't guess."
                ))
                refused += 1
                continue

            tag = "CULL" if apply else "would cull"
            self.stdout.write(
                f"  [{tag}]  pk={pk} {recorded_name!r} "
                f"({vote_count} votes; {sampled} opinions re-verified clean)"
            )
            if apply:
                with transaction.atomic():
                    n, _ = vote_qs.delete()
                    judge.delete()
                votes_deleted += n
            else:
                votes_deleted += vote_count
            culled += 1

        mode = "APPLIED" if apply else "DRY RUN"
        self.stdout.write(self.style.SUCCESS(
            f"\n{mode}: culled={culled} (votes {votes_deleted:,}), "
            f"refused={refused}, already-gone={missing}."
        ))
        if not apply:
            self.stdout.write("Re-run with --apply to commit.")
