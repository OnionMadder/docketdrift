"""Split the Arizona Court of Appeals into its two Divisions and assign every
COA opinion + judge to the right one. Dry-run by default; --apply to commit.

Why: AZ's COA is two real courts -- Division One (Phoenix) and Division Two
(Tucson) -- that we had collapsed into a single 'Arizona Court of Appeals'
row. The docket number states the division unambiguously ('1 CA-...' = Div 1,
'2 CA-...' = Div 2), and judges sit almost entirely in one division, so the
split is clean.

What it does (idempotent -- safe to re-run after each AZ ingest):
  1. Ensures both division Court rows exist. The existing combined COA row
     (courtlistener_id 'arizctapp') BECOMES Division One; a Division Two row
     is created ('arizctapp-2'). CL ingests keyed on 'arizctapp' therefore
     land new opinions on Div 1, and this command re-homes the Div 2 ones --
     which is exactly why it should run after an ingest.
  2. Reassigns each COA opinion to its division court by docket prefix, AND
     updates the slim embedding table's court_id in lockstep (it is part of
     that table's clustered key, so a bare Opinion.court_id change would
     leave it stale).
  3. Reassigns each COA judge to their majority division (the roster then
     groups them under Division One / Division Two).

Opinions whose docket carries no '1 CA'/'2 CA' prefix (a lone cl-<id> row,
any genuinely malformed docket) are LEFT on Division One and reported -- never
guessed.
"""
import re
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Court, Judge, Opinion, PanelVote

_LEAD_NO = re.compile(r"^N[Oo][Ss]?\.?\s*")
_DIV = re.compile(r"([12])\s*CA", re.I)


def az_division(case_number: str) -> str | None:
    """'1' or '2' from an AZ COA docket, tolerating 'No. ' prefixes and
    en/em dashes; None if the docket carries no division marker."""
    s = (case_number or "").strip()
    s = _LEAD_NO.sub("", s)
    s = s.replace("–", "-").replace("—", "-")
    m = _DIV.match(s)
    return m.group(1) if m else None


class Command(BaseCommand):
    help = "Split the AZ Court of Appeals into Division One/Two. Dry-run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Commit. Omit for a dry-run preview.")

    def handle(self, *args, apply, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        mode = "APPLY" if apply else "DRY-RUN (nothing changed; pass --apply)"
        self.stdout.write(self.style.SUCCESS(f"assign_az_divisions — {mode}"))

        # --- 1. division courts ---------------------------------------------
        div1 = Court.objects.filter(
            state_id="AZ", level="APPEALS", courtlistener_id="arizctapp"
        ).first()
        if div1 is None:
            self.stderr.write("No AZ COA row with courtlistener_id 'arizctapp'; aborting.")
            return

        div2 = Court.objects.filter(state_id="AZ", level="APPEALS", division="2").first()
        self.stdout.write("\n=== division courts ===")
        self.stdout.write(
            f"  Div 1  <- existing id={div1.id} "
            f"(set division='1', name 'Arizona Court of Appeals, Division One')")
        if div2:
            self.stdout.write(f"  Div 2  exists id={div2.id}")
        else:
            self.stdout.write("  Div 2  CREATE (courtlistener_id 'arizctapp-2')")

        if apply:
            div1.division = "1"
            div1.name = "Arizona Court of Appeals, Division One"
            div1.save(update_fields=["division", "name"])
            if div2 is None:
                div2 = Court.objects.create(
                    state_id="AZ", level=Court.Level.APPEALS, division="2",
                    courtlistener_id="arizctapp-2",
                    name="Arizona Court of Appeals, Division Two",
                    slug="arizona-court-of-appeals-division-two",
                )
        # For dry-run we need a stand-in id for div2 in the plan below.
        div2_id = div2.id if div2 else -2
        coa_ids = [div1.id] + ([div2.id] if div2 else [])

        # --- 2. reassign opinions -------------------------------------------
        # Fetch every COA opinion currently on either division court so a
        # re-run also corrects anything an ingest dropped on the wrong one.
        moves_to_2, moves_to_1, unresolved = [], [], []
        rows = (
            Opinion.objects.filter(court_id__in=coa_ids)
            .values_list("id", "case_number", "court_id", "release_date")
            .iterator(chunk_size=5000)
        )
        for oid, cn, court_id, rd in rows:
            d = az_division(cn)
            if d == "2" and court_id != div2_id:
                moves_to_2.append((oid, court_id, rd))
            elif d == "1" and court_id != div1.id:
                moves_to_1.append((oid, court_id, rd))
            elif d is None:
                unresolved.append((oid, cn, court_id))

        self.stdout.write("\n=== opinion reassignment ===")
        self.stdout.write(f"  -> Division Two: {len(moves_to_2)}")
        self.stdout.write(f"  -> Division One: {len(moves_to_1)}")
        self.stdout.write(f"  no division marker (left on Div 1, reported): {len(unresolved)}")
        for oid, cn, court_id in unresolved[:15]:
            self.stdout.write(f"     id={oid} {cn!r}")

        if apply:
            self._move(moves_to_2, div2.id)
            self._move(moves_to_1, div1.id)
            # Any unresolved rows sitting on the (soon-nonexistent semantics of)
            # combined court already live on div1.id, so nothing to do.

        # --- 3. reassign judges to their majority division ------------------
        jdiv: dict[int, Counter] = {}
        for jid, cn in (
            PanelVote.objects.filter(opinion__court_id__in=coa_ids)
            .values_list("judge_id", "opinion__case_number")
            .iterator(chunk_size=10000)
        ):
            d = az_division(cn)
            if d:
                jdiv.setdefault(jid, Counter())[d] += 1

        jcourt = dict(Judge.objects.filter(state_id="AZ").values_list("id", "court_id"))
        to_d1 = to_d2 = 0
        judge_updates = []
        for jid, cnt in jdiv.items():
            # Only split judges whose HOME court is the combined COA. A judge
            # who sat on the COA and was later elevated to the Supreme Court
            # (Cruz, Beene, Timmer, Montgomery) is now court=Supreme and must
            # NOT be dragged back to a COA division by their historical votes.
            if jcourt.get(jid) != div1.id:
                continue
            target = div1 if cnt.get("1", 0) >= cnt.get("2", 0) else div2
            # Div 1 stays on the existing court row; only Div 2 judges move.
            if target and target.id != div1.id:
                judge_updates.append((jid, target.id))
            if target is div1:
                to_d1 += 1
            else:
                to_d2 += 1
        self.stdout.write("\n=== judge reassignment (by majority division) ===")
        self.stdout.write(f"  Division One: {to_d1}   Division Two: {to_d2}   "
                          f"rows to update: {len(judge_updates)}")
        if apply:
            for jid, cid in judge_updates:
                Judge.objects.filter(pk=jid).update(court_id=cid)

        # --- summary ---------------------------------------------------------
        if apply:
            d1n = Opinion.objects.filter(court_id=div1.id).count()
            d2n = Opinion.objects.filter(court_id=div2.id).count()
            self.stdout.write(self.style.SUCCESS(
                f"\nAPPLIED. Div One opinions={d1n}, Div Two opinions={d2n}, "
                f"{len(judge_updates)} judges re-homed."))
        else:
            self.stdout.write(
                f"\nprojected: Div One {len(moves_to_1)} in / Div Two "
                f"{len(moves_to_2)} in. Re-run with --apply to commit.")

    def _move(self, moves, target_court_id):
        """Move opinions to target_court_id, updating the slim embedding table
        (clustered on court_id) in the same pass. Chunked."""
        if not moves:
            return
        ids = [m[0] for m in moves]
        # main table -- one bulk update per chunk
        for i in range(0, len(ids), 1000):
            chunk = ids[i:i + 1000]
            Opinion.objects.filter(pk__in=chunk).update(court_id=target_court_id)
        # slim embedding table (no Django model; MySQL only). Each source row
        # is addressed by (old court_id, release_date, opinion_id) -- but since
        # court_id is the PK's leading column and we don't have a secondary
        # index, filter by the old court_id + opinion_id chunk (scans only the
        # old court's slim partition, which is small and blob-free).
        if connection.vendor != "mysql":
            return
        by_src: dict[int, list[int]] = {}
        for oid, src_court, _rd in moves:
            by_src.setdefault(src_court, []).append(oid)
        with connection.cursor() as cur:
            for src_court, oids in by_src.items():
                for i in range(0, len(oids), 1000):
                    chunk = oids[i:i + 1000]
                    placeholders = ",".join(["%s"] * len(chunk))
                    cur.execute(
                        f"UPDATE opinions_opinionembedding SET court_id=%s "
                        f"WHERE court_id=%s AND opinion_id IN ({placeholders})",
                        [target_court_id, src_court, *chunk],
                    )
