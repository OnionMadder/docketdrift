"""Merge same-judge duplicate rows: exact-name dupes + surname-only shadows.

Two failure modes leave one judge as several Judge rows, both surfaced by the
hyphenation cleanup:

  * Exact-name duplicate -- the identical full name minted twice
    (e.g. two "William A. Holohan" rows).
  * Surname-only shadow -- a bare-surname row captured before the full name was
    known ("Deconcini") sitting beside the full-name row
    ("Evo Anton DeConcini").

Both are resolved per surname group, and ONLY when the surname has a single
unambiguous identity in that state:

  * If the group has exactly one distinct full name, that full-name row (the
    most-voted, on a tie the lowest id) is the survivor, and every other row in
    the group -- exact-name twins AND bare-surname shadows -- is merged into it.
  * If the group has TWO OR MORE distinct full names (genuinely different judges
    who share a surname, e.g. two unrelated "Johnson"s), the bare-surname rows
    are ambiguous -- we can't know which judge they belong to -- so they are
    left alone and reported. Exact-name twins within that group are still
    collapsed (identical name = same person).

This never guesses across conflicting first names, so it will not fuse two
distinct people. Votes are reassigned via opinions.judge_merge.merge_judge
(collision dedup keeps the stronger vote type). Dry-run by DEFAULT; --apply
commits.
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count

from opinions.judge_merge import merge_judge, metadata_score, surname as _surname
from opinions.models import Judge, PanelVote, State


def _norm(full_name: str) -> str:
    # Lowercase, collapse whitespace, and drop periods/commas so "Fred C.
    # Struckmeyer Jr." and "Fred C Struckmeyer Jr" count as one name.
    return (
        " ".join(full_name.strip().split()).lower().replace(".", "").replace(",", "")
    )


class Command(BaseCommand):
    help = (
        "Merge exact-name duplicate and surname-only-shadow Judge rows into "
        "their canonical full-name row. Dry-run unless --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--state", default=None,
            help="Limit to one state code (e.g. AZ). Default: every state.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Perform the merge + deletions. Omit for a dry-run preview.",
        )

    def handle(self, *args, state, apply, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        if state:
            states = list(State.objects.filter(code=state.upper()))
            if not states:
                self.stderr.write(f"No such state: {state!r}")
                return
        else:
            states = list(State.objects.all())

        mode = "APPLY" if apply else "DRY-RUN (no changes; pass --apply to execute)"
        self.stdout.write(self.style.SUCCESS(f"merge_duplicate_judges — {mode}"))

        total_merged = total_moved = total_deduped = total_skipped = 0

        for st in states:
            judges = list(Judge.objects.filter(state=st))
            vc = {
                r["judge"]: r["n"]
                for r in PanelVote.objects.filter(judge__state=st)
                .values("judge").annotate(n=Count("id"))
            }
            groups: dict[str, list[Judge]] = defaultdict(list)
            for j in judges:
                groups[_surname(j.full_name).lower()].append(j)

            for sur, rows in groups.items():
                if len(rows) < 2:
                    continue
                full_rows = [j for j in rows if len(j.full_name.split()) > 1]
                distinct_full = {_norm(j.full_name) for j in full_rows}

                if len(distinct_full) <= 1:
                    # One identity for the whole surname group: the canonical
                    # survivor is the most-voted row (prefer a full-name row),
                    # everyone else merges into it.
                    pool = full_rows or rows
                    survivor = max(
                        pool,
                        key=lambda j: (metadata_score(j), vc.get(j.pk, 0), -j.pk),
                    )
                    losers = [j for j in rows if j.pk != survivor.pk]
                    for loser in losers:
                        m, d = merge_judge(loser, survivor, apply)
                        total_merged += 1
                        total_moved += m
                        total_deduped += d
                        self.stdout.write(
                            f"  [{st.code}] {loser.full_name!r} (id={loser.pk}, "
                            f"{vc.get(loser.pk, 0)}v) -> {survivor.full_name!r} "
                            f"(id={survivor.pk}): {m} moved, {d} deduped"
                            + ("" if apply else "  [preview]")
                        )
                else:
                    # Conflicting full names share this surname. Collapse only
                    # EXACT-name twins (same person); leave bare-surname rows.
                    by_name: dict[str, list[Judge]] = defaultdict(list)
                    for j in full_rows:
                        by_name[_norm(j.full_name)].append(j)
                    for name, twins in by_name.items():
                        if len(twins) < 2:
                            continue
                        survivor = max(
                            twins,
                            key=lambda j: (metadata_score(j), vc.get(j.pk, 0), -j.pk),
                        )
                        for loser in twins:
                            if loser.pk == survivor.pk:
                                continue
                            m, d = merge_judge(loser, survivor, apply)
                            total_merged += 1
                            total_moved += m
                            total_deduped += d
                            self.stdout.write(
                                f"  [{st.code}] {loser.full_name!r} (id={loser.pk}) -> "
                                f"{survivor.full_name!r} (id={survivor.pk}): "
                                f"{m} moved, {d} deduped  [exact-dup]"
                                + ("" if apply else "  [preview]")
                            )
                    bare = [j for j in rows if len(j.full_name.split()) == 1]
                    if bare:
                        total_skipped += len(bare)
                        self.stdout.write(
                            f"  [{st.code}] SKIP surname-only {[j.full_name for j in bare]!r} "
                            f"— {len(distinct_full)} distinct full names share "
                            f"'{sur}' ({', '.join(sorted(distinct_full))}); ambiguous"
                        )

        self.stdout.write(self.style.SUCCESS(
            f"\n{total_merged} row(s) merged: {total_moved} votes moved, "
            f"{total_deduped} deduped, {total_skipped} surname-only skipped (ambiguous)."
            + ("" if apply else "  Re-run with --apply to commit.")
        ))
