"""Merge duplicate Opinion rows -- the same decision stored twice under two
spellings of one docket number.

Where the pairs come from (both are ingest-history artifacts, not bugs in the
sources):

  * Canonical-collision pairs: one row stored under a malformed spelling
    ('a230380', 'NO. A15-178') and one under the canonical form the PDF parser
    emits ('A23-0380'). `normalize_case_numbers` deliberately SKIPS these --
    renaming would collide -- and its 2026-08-04 dry run counted ~1,480.
  * cl-<id> caption pairs: an AZ row stored under CourtListener's synthetic
    cluster id whose own caption names a docket another row already holds
    (fed in via --pairs-file, discovered by the 2026-08-06 cl-id repair).

SAFETY GATES -- a pair is merged ONLY when all three hold, else it is
reported and skipped:

  1. Same court (definitionally true for both discovery paths).
  2. Same release_date. The 2026-08-04 classification found 508 same-docket
     pairs with DIFFERENT dates -- an opinion and a later amended opinion or
     order on the same docket. Those are two REAL documents; a date-blind
     merge would destroy 508 of them. They are never eligible here.
  3. Compatible titles: after normalization (casefold, punctuation stripped,
     v./vs. unified), one title's token set must be contained >= 0.5 in the
     other's, or one must be empty. 'State v. Tittle' vs 'State of Arizona v.
     Robert Tittle' passes; two genuinely different same-day captions do not.

Survivor choice: the row whose case_number is already canonical (that is the
URL a lawyer can reach). If both are canonical (cl- pairs), the non-synthetic
row wins. The loser's data is carried forward, never blindly deleted:

  * Scalar fields (reporter_cite, courtlistener_id, disposition, holdings,
    review metadata, pdf_file, html_content, source_url...) fill EMPTY
    survivor fields only -- an editor's work on the survivor is never
    overwritten.
  * Inbound citation edges (other opinions citing the loser) are re-pointed
    at the survivor -- these are real graph edges that a bare delete would
    cascade away. Outbound edges re-point too, deduped against the
    survivor's own; self-edges that would result are dropped.
  * PanelVote / ParallelCite / TagSuggestion re-point with unique-constraint
    dedup (survivor's own row wins). StatuteCitation and OpinionHolding
    re-point only when the survivor has NONE (they are per-text derivations;
    mixing two extractions of the same text would double-count).
  * Applied tags union. ParseLog re-points (audit trail).
  * The loser's slim-embedding-table row is deleted (raw SQL -- the table has
    no Django model), then the loser row itself.

Dry-run by DEFAULT; --apply commits. Each pair applies in one transaction.
NFSN: lifts max_statement_time; --limit bounds a run so it self-exits under
the CPU cull (drive chunks from outside, per the ops discipline).
"""

import re
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from opinions.case_numbers import canonical_case_number
from opinions.models import (
    Opinion,
    OpinionCitation,
    OpinionHolding,
    PanelVote,
    ParallelCite,
    ParseLog,
    StatuteCitation,
    TagSuggestion,
)

_SYNTHETIC = re.compile(r"^cl-\d+$", re.I)
_TITLE_NOISE = frozenset(
    "of the in re matter a an and et al state arizona minnesota new hampshire".split()
)

# Scalar fields carried loser -> survivor when the survivor's value is empty.
# Deliberately NOT raw_text: both rows hold the same decision's text and the
# survivor's is the one its derived layers (statutes, holdings, citations,
# embedding) were computed from -- swapping text under them would desync.
_CARRY_FIELDS = (
    "reporter_cite",
    "courtlistener_id",
    "disposition",
    "disposition_bucket",
    "html_content",
    "source_url",
    "holding_summary",
    "holding_source_paras",
    "holding_review_status",
    "holding_reviewed_by",
    "holding_reviewed_at",
    "holding_extracted_at",
    "holding_model",
    "reviewed_by",
    "reviewed_at",
    "review_notes",
    "pdf_file",
)


def _title_tokens(title: str) -> set[str]:
    t = re.sub(r"[^\w\s]", " ", (title or "").lower())
    t = re.sub(r"\bvs?\b", " ", t)
    return {w for w in t.split() if w not in _TITLE_NOISE}


def titles_compatible(a: str, b: str) -> bool:
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return True  # a missing caption is the poorer row, not a conflict
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(small & big) / len(small) >= 0.5


def merge_opinion(loser: Opinion, survivor: Opinion, apply: bool) -> dict:
    """Fold `loser` into `survivor`. Returns per-table action counts.

    Caller has already verified the safety gates. With apply=False this only
    COUNTS what would happen -- nothing is written.
    """
    n = Counter()

    # --- scalar carry-forward -------------------------------------------
    changed = []
    for f in _CARRY_FIELDS:
        if not getattr(survivor, f, None) and getattr(loser, f, None):
            changed.append(f)
            if apply:
                setattr(survivor, f, getattr(loser, f))
    n["fields_carried"] = len(changed)

    # --- panel votes: unique (opinion, judge), survivor's vote wins ------
    have = set(survivor.panel_votes.values_list("judge_id", flat=True))
    for pv in loser.panel_votes.all():
        if pv.judge_id in have:
            n["votes_deduped"] += 1
            if apply:
                pv.delete()
        else:
            n["votes_moved"] += 1
            if apply:
                pv.opinion = survivor
                pv.save(update_fields=["opinion"])

    # --- per-text derivations: move only if the survivor has none --------
    for related, key in ((StatuteCitation, "statutes"), (OpinionHolding, "holdings")):
        s_qs = related.objects.filter(opinion=survivor).order_by()
        l_qs = related.objects.filter(opinion=loser).order_by()
        l_count = l_qs.count()
        if not l_count:
            continue
        if s_qs.exists():
            n[f"{key}_dropped"] += l_count  # survivor has its own extraction
        else:
            n[f"{key}_moved"] += l_count
            if apply:
                l_qs.update(opinion=survivor)

    # --- citation edges: OUTBOUND (loser cites X) ------------------------
    seen_out = {
        (c.cited_opinion_id, c.cited_reference, c.source)
        for c in survivor.citations_made.all()
    }
    for c in loser.citations_made.all():
        if c.cited_opinion_id == survivor.pk:  # would become a self-edge
            n["edges_self_dropped"] += 1
            if apply:
                c.delete()
        elif (c.cited_opinion_id, c.cited_reference, c.source) in seen_out:
            n["edges_out_deduped"] += 1
            if apply:
                c.delete()
        else:
            n["edges_out_moved"] += 1
            if apply:
                c.citing_opinion = survivor
                c.save(update_fields=["citing_opinion"])

    # --- citation edges: INBOUND (X cites loser) -- the valuable ones ----
    seen_in = {
        (c.citing_opinion_id, c.source)
        for c in survivor.citations_received.all()
    }
    for c in loser.citations_received.all():
        if c.citing_opinion_id == survivor.pk:
            n["edges_self_dropped"] += 1
            if apply:
                c.delete()
        elif (c.citing_opinion_id, c.source) in seen_in:
            n["edges_in_deduped"] += 1
            if apply:
                c.delete()
        else:
            n["edges_in_moved"] += 1
            if apply:
                c.cited_opinion = survivor
                c.save(update_fields=["cited_opinion"])

    # --- parallel cites: unique (opinion, cite) --------------------------
    have = set(ParallelCite.objects.filter(opinion=survivor).values_list("cite", flat=True))
    for pc in ParallelCite.objects.filter(opinion=loser):
        if pc.cite in have:
            n["parallel_deduped"] += 1
            if apply:
                pc.delete()
        else:
            n["parallel_moved"] += 1
            if apply:
                pc.opinion = survivor
                pc.save(update_fields=["opinion"])

    # --- tag suggestions: unique (opinion, tag) --------------------------
    have = set(TagSuggestion.objects.filter(opinion=survivor).values_list("tag_id", flat=True))
    for tsug in TagSuggestion.objects.filter(opinion=loser):
        if tsug.tag_id in have:
            n["tagsugg_deduped"] += 1
            if apply:
                tsug.delete()
        else:
            n["tagsugg_moved"] += 1
            if apply:
                tsug.opinion = survivor
                tsug.save(update_fields=["opinion"])

    # --- applied tags (M2M union) + parse logs ---------------------------
    loser_tags = list(loser.tags.all())
    n["tags_unioned"] = len(loser_tags)
    n["parselogs_moved"] = ParseLog.objects.filter(opinion=loser).count()
    if apply:
        if loser_tags:
            survivor.tags.add(*loser_tags)
        ParseLog.objects.filter(opinion=loser).update(opinion=survivor)
        if changed:
            survivor.save(update_fields=changed)
        # slim embedding table has no Django model -- raw SQL, and MariaDB-only
        # (local SQLite dev has no such table).
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM opinions_opinionembedding WHERE opinion_id = %s",
                    [loser.pk],
                )
        loser.delete()

    return n


class Command(BaseCommand):
    help = "Merge duplicate Opinion rows (two spellings of one docket). Dry-run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--state", default=None, help="Limit to one state code.")
        parser.add_argument(
            "--pairs-file", default=None,
            help="TSV of explicit 'loser_id<TAB>survivor_id' pairs to merge "
                 "(e.g. the cl-<id> caption collisions). Safety gates still apply.",
        )
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Merge at most N pairs this run (chunked driving under the NFSN cull).",
        )
        parser.add_argument("--apply", action="store_true",
                            help="Commit. Omit for a dry-run preview.")

    def handle(self, *args, state, pairs_file, limit, apply, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        mode = "APPLY" if apply else "DRY-RUN (no changes; pass --apply to execute)"
        self.stdout.write(self.style.SUCCESS(f"merge_duplicate_opinions — {mode}"))

        pairs = (
            self._pairs_from_file(pairs_file)
            if pairs_file
            else self._discover_pairs(state)
        )
        self.stdout.write(f"candidate pairs: {len(pairs)}")

        merged = skipped = 0
        totals = Counter()
        for loser, survivor in pairs:
            if limit is not None and merged >= limit:
                self.stdout.write(f"--limit {limit} reached; stopping this chunk.")
                break

            reason = self._skip_reason(loser, survivor)
            if reason:
                skipped += 1
                self.stdout.write(
                    f"  SKIP [{loser.court.state_id}] {loser.case_number!r} (id={loser.pk}) / "
                    f"{survivor.case_number!r} (id={survivor.pk}): {reason}"
                )
                continue

            if apply:
                with transaction.atomic():
                    n = merge_opinion(loser, survivor, apply=True)
            else:
                n = merge_opinion(loser, survivor, apply=False)
            merged += 1
            totals.update(n)
            acted = ", ".join(f"{k}={v}" for k, v in sorted(n.items()) if v)
            self.stdout.write(
                f"  [{loser.court.state_id}] {loser.case_number!r} (id={loser.pk}) -> "
                f"{survivor.case_number!r} (id={survivor.pk}): {acted or 'nothing to move'}"
                + ("" if apply else "  [preview]")
            )

        summary = ", ".join(f"{k}={v}" for k, v in sorted(totals.items()) if v)
        self.stdout.write(self.style.SUCCESS(
            f"\n{merged} pair(s) merged, {skipped} skipped. {summary}"
            + ("" if apply else "  Re-run with --apply to commit.")
        ))

    # ------------------------------------------------------------------
    def _skip_reason(self, loser, survivor):
        if loser.court_id != survivor.court_id:
            return "different courts"
        if loser.release_date != survivor.release_date:
            return (f"different dates ({loser.release_date} vs {survivor.release_date}) "
                    "— two real documents on one docket, never merged")
        if not titles_compatible(loser.title, survivor.title):
            return f"titles conflict ({loser.title[:40]!r} vs {survivor.title[:40]!r})"
        return None

    def _pairs_from_file(self, path):
        pairs = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                lid, sid = line.split("\t")[:2]
                try:
                    loser = Opinion.objects.get(pk=int(lid))
                    survivor = Opinion.objects.get(pk=int(sid))
                except Opinion.DoesNotExist:
                    self.stdout.write(f"  SKIP pair {lid}/{sid}: row gone (already merged?)")
                    continue
                pairs.append((loser, survivor))
        return pairs

    def _discover_pairs(self, state):
        """Canonical-collision discovery: a malformed row whose canonical form
        exists on the same court. The malformed row is always the loser --
        the canonical spelling is the reachable URL."""
        court_filter = {}
        if state:
            court_filter["court__state__code"] = state.upper()

        pairs = []
        qs = (
            Opinion.objects.filter(**court_filter)
            .only("id", "case_number", "court_id")
            .order_by("id")
        )
        # Two passes over ids to stay off the fat table: first collect the
        # malformed -> canonical mapping cheaply, then fetch full rows for
        # just the pair members.
        malformed = []
        for oid, cn, court_id in qs.values_list("id", "case_number", "court_id").iterator(chunk_size=5000):
            canon = canonical_case_number(cn)
            if canon != cn:
                malformed.append((oid, cn, court_id, canon))

        for oid, cn, court_id, canon in malformed:
            survivor = Opinion.objects.filter(court_id=court_id, case_number=canon).first()
            if survivor is None:
                continue  # normalize_case_numbers territory, not a pair
            loser = Opinion.objects.get(pk=oid)
            pairs.append((loser, survivor))
        return pairs
