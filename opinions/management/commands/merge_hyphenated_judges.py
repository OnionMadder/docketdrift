"""Merge PDF line-break hyphenation-artifact Judge rows into their clean twin.

pypdf extraction preserves the soft hyphen a PDF inserts when a surname wraps
across a line break, so a byline that reads "Struck-\nmeyer" ingests as the
surname "Struck-meyer". ``resolve_judges`` then mints that as its own Judge row,
a duplicate of the correctly-spelled "Struckmeyer" captured from opinions where
the name didn't wrap. Same judge, two rows, votes split between them.

Detection is deliberately narrow so a GENUINE hyphenated surname is never
touched: a hyphenated row is only merged when removing the hyphen yields the
surname of ANOTHER existing judge in the same state (the clean twin). A real
"Smith-Jones" has no "SmithJones" twin, so it is left alone.

The clean (non-hyphenated) row is always the survivor. Every PanelVote on the
hyphenated row is reassigned to the survivor; where the survivor already voted
on that opinion (the unique_together on (opinion, judge) forbids a duplicate),
the stronger vote type is kept (author beats join) and the loser's row dropped.
Then the hyphenated Judge row is deleted.

Dry-run by DEFAULT (it deletes rows); pass --apply to execute.
"""

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.judge_merge import merge_judge, surname as _surname
from opinions.models import Judge, PanelVote, State


class Command(BaseCommand):
    help = (
        "Merge hyphenation-artifact Judge rows (e.g. 'Struck-meyer') into their "
        "clean twin ('Struckmeyer'), moving panel votes. Dry-run unless --apply."
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
        # Batch command: lift settings' 25s web-tier cap so the roster scan +
        # per-judge vote reads don't get KILLed under contention (errno 1969).
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
        self.stdout.write(self.style.SUCCESS(f"merge_hyphenated_judges — {mode}"))

        total_pairs = total_moved = total_deduped = total_skipped = 0

        for st in states:
            judges = list(Judge.objects.filter(state=st))
            # surname(lower) -> [judges] for the clean-twin lookup.
            by_surname: dict[str, list[Judge]] = {}
            for j in judges:
                by_surname.setdefault(_surname(j.full_name).lower(), []).append(j)

            for loser in judges:
                sur = _surname(loser.full_name)
                if "-" not in sur:
                    continue
                dehyph = sur.replace("-", "").lower()
                if not dehyph:
                    continue
                candidates = [
                    j for j in by_surname.get(dehyph, [])
                    if j.pk != loser.pk and "-" not in _surname(j.full_name)
                ]
                if not candidates:
                    # No clean twin -> a genuine hyphenated surname. Leave it.
                    continue

                # Multiple clean twins happen when the same judge ALSO exists as
                # a surname-only row and a full-name row (e.g. "Deconcini" +
                # "Evo Anton DeConcini"). Those are name-COMPATIBLE (one full
                # name + surname-only variants) -> safe to merge the hyphen
                # artifact into the fullest row. But two DIFFERENT full names
                # (conflicting first names) mean genuinely distinct judges who
                # merely share a surname -> refuse to guess.
                full_twins = [c for c in candidates if len(c.full_name.split()) > 1]
                distinct_full = {c.full_name.strip().lower() for c in full_twins}
                if len(distinct_full) > 1:
                    total_skipped += 1
                    self.stdout.write(
                        f"  [{st.code}] SKIP {loser.full_name!r}: conflicting twins "
                        f"({', '.join(sorted(c.full_name for c in full_twins))}) — resolve by hand"
                    )
                    continue

                # Survivor = the most complete, most-established row: prefer a
                # full-name twin, break ties by vote count, then lowest id.
                pool = full_twins or candidates
                vc = {c.pk: PanelVote.objects.filter(judge=c).count() for c in pool}
                survivor = max(pool, key=lambda c: (vc[c.pk], -c.pk))

                moved, deduped = merge_judge(loser, survivor, apply)
                total_pairs += 1
                total_moved += moved
                total_deduped += deduped
                residual = [c for c in candidates if c.pk != survivor.pk]
                note = ""
                if residual:
                    note = (
                        "  (residual non-hyphen dup still present: "
                        + ", ".join(f"{c.full_name!r} id={c.pk}" for c in residual) + ")"
                    )
                self.stdout.write(
                    f"  [{st.code}] {loser.full_name!r} (id={loser.pk}) -> "
                    f"{survivor.full_name!r} (id={survivor.pk}): "
                    f"{moved} votes moved, {deduped} deduped"
                    + ("" if apply else "  [preview]") + note
                )

        self.stdout.write(self.style.SUCCESS(
            f"\n{total_pairs} pair(s): {total_moved} votes moved, "
            f"{total_deduped} deduped, {total_skipped} skipped (ambiguous)."
            + ("" if apply else "  Re-run with --apply to commit.")
        ))
