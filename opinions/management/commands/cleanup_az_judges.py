"""One-shot AZ judge cleanup -- Tier 1 (high-confidence only). Dry-run default.

Background: AZ carried 247 judge rows vs MN 122 / NH 37. A 2026-08-07 audit
found the inflation is three extraction defects, not real judges:

  * junk tokens  -- the byline/panel parser captured non-name words
                    ("And", "Appel", "Opinion", "State", a lone "M").
  * cross-court leaks -- judges of OTHER courts, cited in an AZ opinion's text
                    inside a parenthetical "(Name, J., concurring)" next to a
                    reporter cite, minted as AZ panelists (Kozinski/9th Cir,
                    Dietzen/MN, Titone/NY, "Sealia"=Scalia, ...). Same class as
                    the July Souter/Connor/Burger cull; these are the non-SCOTUS
                    ones the stoplist never covered.
  * OCR variants -- one real AZ judge split into corrupted spellings by bad
                    OCR on old scans (Údall/Udaljl -> Udall, Donoprio/Donofrlo
                    -> Donofrio, Eockwood/Lockwoód -> Lockwood).

This command actions ONLY the verdicts that were verified row-by-row against
the opinion text (see the audit). The lists below are DATA, not live
heuristics -- each entry was eyeballed, so the command can't silently
re-classify as the corpus changes. Deliberately EXCLUDED and left for the
human roster pass: distance-2 "OCR" guesses (noisy -- Fink!=King, Olson!=Nelson,
Arabian is a California justice), the real 19th-c. Territorial justices
(Tweed/Sloan/Stilwell/Pinney -- bare panel signoffs, KEEP), and byline-real
surname-only judges that just need a full name (Vásquez, Staring, ...).

Safety rails:
  * Every row is name-checked before it is touched; a mismatch (pk drift)
    skips it with a warning rather than acting on the wrong judge.
  * DELETE/CULL refuse any row that carries curated metadata (bio, photo,
    courtlistener_id, or cl_absent) -- those are never extraction junk.
  * MERGE routes through opinions.judge_merge.merge_judge, which carries
    metadata forward and dedups colliding votes.

Dry-run by default; pass --apply to execute. Idempotent: re-running after an
apply is a no-op (the rows are gone / already merged).
"""
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from opinions.judge_merge import merge_judge
from opinions.models import Judge, PanelVote

# (pk, expected surname) -- non-name tokens the parser mistook for judges.
DELETE = [
    (431, "Trade"), (421, "Opinion"), (456, "M"), (344, "State"),
    (442, "Silent"), (438, "One"), (418, "Judge"), (392, "Ini"),
    (427, "Hon"), (465, "Appel"), (430, "And"),
]

# (pk, expected surname) -- other courts' judges cited in AZ opinion text.
# Each verified as a parenthetical "(Name, J., concurring)" citation.
CULL = [
    (379, "Mansfield"), (413, "Dietzen"), (422, "Willett"), (426, "Wilkins"),
    (455, "Watford"), (462, "Wardlaw"), (458, "Titone"), (473, "Smith"),
    (483, "Sealia"), (439, "Ripple"), (365, "Madsen"), (428, "Liacos"),
    (477, "Letts"), (466, "Leslie"), (382, "Kozinski"), (478, "Hobbs"),
    (423, "Heffernan"), (394, "Glaze"), (389, "Gesell"), (360, "Chasanow"),
    (420, "Calogero"), (446, "Tjoflat"), (384, "Murnaghan"), (363, "Ginsberg"),
]

# (loser pk, loser name, survivor pk, survivor name) -- corrupted-looking
# distance-1 OCR variants folded into their canonical real row.
MERGE = [
    (411, "Údall", 186, "Jesse A. Udall"),
    (364, "Udaljl", 186, "Jesse A. Udall"),
    (415, "Roeb", 213, "Donald F. Froeb"),
    (472, "Portly", 245, "Maurice Portley"),
    (461, "Mefarland", 190, "Ernest W. McFarland"),
    (499, "Lockwoód", 177, "Lorna E. Lockwood"),
    (378, "Eockwood", 177, "Lorna E. Lockwood"),
    (412, "Donoprio", 206, "Francis J. Donofrio"),
    (435, "Donofrlo", 206, "Francis J. Donofrio"),
    (373, "Bindes", 188, "Dudley W. Windes"),
]

_CURATED = ("bio_summary", "photo_url", "courtlistener_id")


class Command(BaseCommand):
    help = "One-shot AZ judge cleanup (Tier 1). Dry-run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Execute. Omit for a dry-run preview.")

    def handle(self, *args, apply, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        mode = "APPLY" if apply else "DRY-RUN (nothing changed; pass --apply)"
        self.stdout.write(self.style.SUCCESS(f"cleanup_az_judges — {mode}"))

        def load(pk, expected):
            """Fetch a Judge, verifying its surname still matches (pk-drift guard)."""
            j = Judge.objects.filter(pk=pk, state_id="AZ").first()
            if j is None:
                self.stdout.write(f"  SKIP id={pk}: gone (already cleaned?)")
                return None
            if j.full_name.split()[-1] != expected:
                self.stdout.write(self.style.WARNING(
                    f"  SKIP id={pk}: name drift (is {j.full_name!r}, "
                    f"expected surname {expected!r}) — NOT touching"))
                return None
            return j

        def curated(j):
            return any(getattr(j, f, "") for f in _CURATED) or getattr(j, "cl_absent", False)

        deleted = culled = merged = votes_removed = votes_moved = skipped = 0

        # --- DELETE + CULL: same mechanism, different reason label ----------
        for label, rows in (("DELETE", DELETE), ("CULL", CULL)):
            self.stdout.write(f"\n=== {label} ({len(rows)} rows) ===")
            for pk, expected in rows:
                j = load(pk, expected)
                if j is None:
                    skipped += 1
                    continue
                if curated(j):
                    self.stdout.write(self.style.WARNING(
                        f"  SKIP {j.full_name!r} (id={pk}): carries curated "
                        f"metadata — refusing to delete"))
                    skipped += 1
                    continue
                nv = PanelVote.objects.filter(judge=j).count()
                self.stdout.write(
                    f"  {label.lower()} {j.full_name!r} (id={pk}): "
                    f"remove {nv} vote(s) + row" + ("" if apply else "  [preview]"))
                if apply:
                    with transaction.atomic():
                        PanelVote.objects.filter(judge=j).delete()
                        j.delete()
                votes_removed += nv
                if label == "DELETE":
                    deleted += 1
                else:
                    culled += 1

        # --- MERGE: OCR variant -> canonical real judge ---------------------
        self.stdout.write(f"\n=== MERGE ({len(MERGE)} rows) ===")
        for lpk, lname, spk, sname in MERGE:
            loser = load(lpk, lname.split()[-1])
            survivor = Judge.objects.filter(pk=spk, state_id="AZ").first()
            if loser is None:
                skipped += 1
                continue
            if survivor is None or survivor.full_name != sname:
                self.stdout.write(self.style.WARNING(
                    f"  SKIP merge id={lpk}: survivor id={spk} missing or renamed "
                    f"(expected {sname!r}) — NOT merging"))
                skipped += 1
                continue
            nv = PanelVote.objects.filter(judge=loser).count()
            self.stdout.write(
                f"  merge {loser.full_name!r} (id={lpk}, {nv}v) -> "
                f"{survivor.full_name!r} (id={spk})" + ("" if apply else "  [preview]"))
            if apply:
                with transaction.atomic():
                    moved, _ = merge_judge(loser, survivor, apply=True)
                    votes_moved += moved
            merged += 1

        before = Judge.objects.filter(state_id="AZ").count()
        projected = before - (0 if apply else (deleted + culled + merged))
        self.stdout.write(self.style.SUCCESS(
            f"\n{deleted} deleted, {culled} culled, {merged} merged, "
            f"{skipped} skipped. votes removed={votes_removed}, moved={votes_moved}."))
        if apply:
            self.stdout.write(self.style.SUCCESS(
                f"AZ judges now: {before}"))
        else:
            self.stdout.write(
                f"AZ judges: {before} -> {projected} projected after --apply.")
