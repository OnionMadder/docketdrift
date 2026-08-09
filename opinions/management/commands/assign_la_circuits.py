"""Split the LA COA landing court into its 5 real circuits and assign every
COA opinion to its own circuit. Dry-run by default; --apply to commit.

Why: CourtListener collapses all 5 Louisiana COA circuits under ONE court id
(``lactapp``), so the Phase 7 bulk load lands every COA opinion on the
First-Circuit row (which is the LA "landing" court, matching CL's lactapp
id -- see migration 0037). This command re-homes each opinion to its real
circuit court row. The remaining four rows (2nd..5th) were seeded with
synthetic CL ids ``lactapp-2``..``lactapp-5`` in Phase 1.

Signal extraction, in order of preference (see the Phase 0 recon doc):
  1. **PDF header regex** on the first ~2000 chars of raw_text --
     LA COA opinions universally open with e.g.
     ``STATE OF LOUISIANA / COURT OF APPEAL / FIRST CIRCUIT``.
     Highest confidence; deterministic.
  2. **Parish fallback** -- extract the parish name from the caption
     (Louisiana appellate captions carry ``(Parish of X)``), look it
     up in the 64-entry PARISH_TO_CIRCUIT map (La. R.S. 13:312).
     Every LA parish belongs to exactly one COA circuit by statute,
     so this is deterministic when the parish is legible.

If both signals disagree, the opinion is LEFT on the landing court and
REPORTED -- never guessed. Same discipline as assign_az_divisions'
"malformed docket" bucket.

Idempotent, resume-safe, cull-safe. Re-runnable after every bulk ingest
that lands new lactapp opinions. Supports ``--max-runtime N`` because
processing 341K rows exceeds NFSN's ~10-minute wallclock cull; each
invocation self-exits well under that and the next tick picks up
naturally (opinions already moved off the landing court are simply
skipped by the query filter).

Slim embedding table (`opinions_opinionembedding`) is updated in
lockstep because ``court_id`` is part of that table's clustered PK;
a bare Opinion.court_id change would leave it stale.

Judge reassignment is deliberately NOT part of this pass -- LA judges
aren't seeded with court FKs on load (CL's people DB rarely includes
LA COA circuit assignments), and Phase 8's ``resolve_judges`` will
mint + attach LA judges from bylines. Judge->circuit mapping happens
there when the byline text is read.
"""
import re
import time
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models.functions import Substr

from opinions.models import Court, Opinion


# ---------------------------------------------------------------------------
# PDF-header regex: LA COA opinions universally open with
#   STATE OF LOUISIANA
#   COURT OF APPEAL
#   FIRST CIRCUIT   (or SECOND / THIRD / FOURTH / FIFTH)
#
# Tolerates: extra whitespace, comma between "APPEAL" and the ordinal
# ("COURT OF APPEAL, FIRST CIRCUIT"), and case (Bluebook uses lower
# "First" in prose, but headers are usually all-caps).
# ---------------------------------------------------------------------------
_HEADER_RE = re.compile(
    r"court\s+of\s+appeal[,\s]+(first|second|third|fourth|fifth)\s+circuit",
    re.IGNORECASE,
)

_ORDINAL_TO_DIV = {
    "first":  "1",
    "second": "2",
    "third":  "3",
    "fourth": "4",
    "fifth":  "5",
}


# ---------------------------------------------------------------------------
# Parish caption regex: LA appellate opinions carry a parenthetical
# "(Parish of X)" (Supreme review) or a caption noun phrase naming the
# parish of origin. Common forms:
#   "(Parish of East Baton Rouge)"
#   "Twenty-Second Judicial District Court, Parish of St. Tammany"
#   "district court for the parish of Orleans"
#
# The regex captures the parish name run following "Parish of ", up to
# the next comma / closing paren / newline. Post-strip: leading "the",
# trailing whitespace / punctuation, and case-fold for the map lookup.
# ---------------------------------------------------------------------------
_PARISH_RE = re.compile(
    r"parish\s+of\s+([A-Za-z][A-Za-z\s.'-]{2,40}?)(?=[,)\n]|\s+(?:and|through|in|from|for|by|to|the))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# La. R.S. 13:312 -- Louisiana's 64 parishes mapped to their COA circuit.
# ---------------------------------------------------------------------------
_PARISH_TO_CIRCUIT: dict[str, str] = {
    # First Circuit (Baton Rouge) -- 16 parishes
    "ascension": "1", "assumption": "1", "east baton rouge": "1",
    "east feliciana": "1", "iberville": "1", "lafourche": "1",
    "livingston": "1", "pointe coupee": "1", "st. helena": "1",
    "st. mary": "1", "st. tammany": "1", "tangipahoa": "1",
    "terrebonne": "1", "washington": "1", "west baton rouge": "1",
    "west feliciana": "1",
    # Second Circuit (Shreveport) -- 23 parishes
    "bienville": "2", "bossier": "2", "caddo": "2", "caldwell": "2",
    "catahoula": "2", "claiborne": "2", "concordia": "2", "de soto": "2",
    "east carroll": "2", "franklin": "2", "jackson": "2", "lincoln": "2",
    "madison": "2", "morehouse": "2", "ouachita": "2", "red river": "2",
    "richland": "2", "sabine": "2", "tensas": "2", "union": "2",
    "webster": "2", "west carroll": "2", "winn": "2",
    # Third Circuit (Lake Charles) -- 18 parishes
    "acadia": "3", "allen": "3", "avoyelles": "3", "beauregard": "3",
    "calcasieu": "3", "cameron": "3", "evangeline": "3", "grant": "3",
    "iberia": "3", "jefferson davis": "3", "lafayette": "3", "lasalle": "3",
    "natchitoches": "3", "rapides": "3", "st. landry": "3", "st. martin": "3",
    "vermilion": "3", "vernon": "3",
    # Fourth Circuit (New Orleans) -- 3 parishes
    "orleans": "4", "plaquemines": "4", "st. bernard": "4",
    # Fifth Circuit (Gretna suburbs) -- 4 parishes
    "jefferson": "5", "st. charles": "5", "st. james": "5",
    "st. john the baptist": "5",
}


def _normalize_parish(raw: str) -> str:
    """Fold a captured parish string to the map key.

    Handles: leading "the", "saint" vs "st." variants, extra whitespace,
    trailing punctuation, and Bluebook capitalization.
    """
    s = (raw or "").strip().lower().rstrip(".,;: )")
    if s.startswith("the "):
        s = s[4:]
    # "Saint X" -> "St. X" (map form)
    s = re.sub(r"^saint\s+", "st. ", s)
    # Collapse runs of whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def detect_circuit(text: str) -> tuple[str | None, str]:
    """Return (division, source) for an LA COA opinion's raw_text.

    Returns:
        ("1".."5", "header")  when the PDF header names the circuit.
        ("1".."5", "parish")  when the caption parish maps to a circuit.
        (None,     "none")    when neither signal is legible.
        (None,     "conflict") when both signals disagree.
    """
    head = (text or "")[:2500]  # first ~2.5KB covers the caption block
    header_div: str | None = None
    parish_div: str | None = None
    parish_name = ""

    m = _HEADER_RE.search(head)
    if m:
        header_div = _ORDINAL_TO_DIV.get(m.group(1).lower())

    pm = _PARISH_RE.search(head)
    if pm:
        parish_name = _normalize_parish(pm.group(1))
        parish_div = _PARISH_TO_CIRCUIT.get(parish_name)

    if header_div and parish_div and header_div != parish_div:
        return None, "conflict"
    return (header_div or parish_div), (
        "header" if header_div else ("parish" if parish_div else "none")
    )


class Command(BaseCommand):
    help = ("Re-home LA COA opinions from the landing court (First Circuit) "
            "to their real circuit. Dry-run unless --apply.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Commit. Omit for a dry-run preview.")
        parser.add_argument("--max-runtime", type=int, default=0,
                            help=("Self-exit after N seconds. 0 = run until "
                                  "done. Set to ~480 on NFSN so each invocation "
                                  "finishes well under the ~10-min wallclock cull."))
        parser.add_argument("--chunk-size", type=int, default=2000,
                            help="Rows per fetch/UPDATE batch.")
        parser.add_argument("--report-limit", type=int, default=25,
                            help="How many unresolved / conflict rows to name.")

    def handle(self, *args, apply, max_runtime, chunk_size, report_limit, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        t0 = time.time()
        mode = "APPLY" if apply else "DRY-RUN (nothing changed; pass --apply)"
        self.stdout.write(self.style.SUCCESS(f"assign_la_circuits — {mode}"))

        # Load all 5 LA COA circuit courts. First Circuit is the landing
        # (courtlistener_id='lactapp'); others are 'lactapp-2'..'lactapp-5'.
        circuits: dict[str, Court] = {}
        for c in Court.objects.filter(
            state_id="LA", level="APPEALS"
        ).order_by("division"):
            circuits[c.division] = c
        if len(circuits) != 5 or set(circuits) != {"1", "2", "3", "4", "5"}:
            self.stderr.write(
                "Expected 5 LA COA circuits ('1'..'5'); found: "
                + repr(sorted(circuits))
            )
            return

        landing_id = circuits["1"].id
        landing_ids = [c.id for c in circuits.values()]  # process any-circuit re-runs too

        # --- classify + queue moves ---------------------------------------
        counts_by_target = Counter()
        counts_by_source = Counter()
        conflicts: list[tuple[int, str]] = []
        unresolved: list[tuple[int, str]] = []
        # {source_court_id: {target_court_id: [opinion_id, ...]}}
        moves: dict[int, dict[int, list[int]]] = {}

        scanned = 0
        # Pull ONLY the first ~2500 chars of raw_text (all the header /
        # caption / parish signals we need). raw_text is a MEDIUMTEXT
        # averaging ~30KB per row on LA; pulling it in full via
        # .only() + .iterator(chunk_size=2000) would build ~60MB per
        # batch and get culled by NFSN in seconds. Substr trims at the
        # SQL layer so each row's payload is <=2500 bytes over the wire.
        qs = (
            Opinion.objects
            .filter(court_id__in=landing_ids)
            .annotate(head=Substr("raw_text", 1, 2500))
            .values("id", "case_number", "court_id", "head")
            .iterator(chunk_size=chunk_size)
        )
        cull = False
        for op in qs:
            scanned += 1
            div, src = detect_circuit(op["head"] or "")
            counts_by_source[src] += 1

            if div is None:
                bucket = conflicts if src == "conflict" else unresolved
                bucket.append((op["id"], op["case_number"] or ""))
                continue

            target = circuits.get(div)
            if target is None or target.id == op["court_id"]:
                # Correct circuit already; nothing to move
                counts_by_target["no-op"] += 1
                continue

            moves.setdefault(op["court_id"], {}).setdefault(target.id, []).append(op["id"])
            counts_by_target[div] += 1

            # Cull-safe: periodic check so we self-exit under NFSN's
            # ~10-min wallclock cap.
            if max_runtime and scanned % 5000 == 0:
                if time.time() - t0 > max_runtime:
                    cull = True
                    self.stdout.write(
                        f"  --max-runtime reached at scanned={scanned:,}; "
                        f"flushing partial batch and exiting."
                    )
                    break
            # Occasional live-progress line so nohup+tail is watchable.
            if scanned % 20000 == 0:
                self.stdout.write(
                    f"  scanned {scanned:,}  ({time.time()-t0:.0f}s)"
                )

        # --- report --------------------------------------------------------
        elapsed = time.time() - t0
        self.stdout.write("\n=== classification ===")
        self.stdout.write(
            f"  scanned:              {scanned:,} in {elapsed:.1f}s"
        )
        for src, n in sorted(counts_by_source.items(), key=lambda x: -x[1]):
            self.stdout.write(f"  signal source {src:>8}: {n:,}")

        self.stdout.write("\n=== target circuit distribution ===")
        for div in ("1", "2", "3", "4", "5", "no-op"):
            if counts_by_target[div]:
                label = f"Div {div}" if div != "no-op" else "already correct"
                self.stdout.write(f"  -> {label:<18} {counts_by_target[div]:,}")

        self.stdout.write("\n=== unresolved / conflict (LEFT on current court) ===")
        self.stdout.write(f"  conflicts (header vs parish disagree): {len(conflicts)}")
        for oid, cn in conflicts[:report_limit]:
            self.stdout.write(f"     id={oid} {cn!r}")
        self.stdout.write(f"  no signal (neither header nor parish):  {len(unresolved)}")
        for oid, cn in unresolved[:report_limit]:
            self.stdout.write(f"     id={oid} {cn!r}")

        # --- apply ---------------------------------------------------------
        if not apply:
            self.stdout.write(
                f"\nprojected moves: {sum(len(v) for m in moves.values() for v in m.values()):,}. "
                f"Re-run with --apply to commit."
            )
            return

        total_moves = sum(len(v) for m in moves.values() for v in m.values())
        self.stdout.write(f"\n=== applying {total_moves:,} moves ===")
        moved = 0
        for src_id, by_target in moves.items():
            for target_id, oids in by_target.items():
                self._move(oids, src_id, target_id, chunk_size)
                moved += len(oids)

        # --- verify + summarize -------------------------------------------
        self.stdout.write("\n=== per-circuit opinion counts (post-apply) ===")
        for div in ("1", "2", "3", "4", "5"):
            c = circuits[div]
            n = Opinion.objects.filter(court_id=c.id).count()
            self.stdout.write(f"  Div {div}  {c.short_label:<30}  {n:,}")

        self.stdout.write(self.style.SUCCESS(
            f"\nAPPLIED. moved={moved:,} scanned={scanned:,} "
            f"elapsed={time.time()-t0:.1f}s "
            f"{'(cull-exited; re-run to continue)' if cull else ''}"
        ))

    def _move(self, opinion_ids: list[int], src_court_id: int,
              target_court_id: int, chunk_size: int) -> None:
        """Move opinions to target_court_id, updating the slim embedding
        table (clustered on court_id) in lockstep. Chunked."""
        if not opinion_ids:
            return

        # Main opinion table
        for i in range(0, len(opinion_ids), chunk_size):
            chunk = opinion_ids[i:i + chunk_size]
            Opinion.objects.filter(pk__in=chunk).update(court_id=target_court_id)

        # Slim embedding table (no Django model; MySQL only). court_id is
        # part of the clustered PK -- filter by (old court_id + opinion_id
        # chunk) so we only touch the old court's partition, not the whole
        # 128K+ table. Same pattern documented in CLAUDE.md's "One
        # non-covered column beside a court_id filter" gotcha.
        if connection.vendor != "mysql":
            return
        with connection.cursor() as cur:
            for i in range(0, len(opinion_ids), chunk_size):
                chunk = opinion_ids[i:i + chunk_size]
                placeholders = ",".join(["%s"] * len(chunk))
                cur.execute(
                    f"UPDATE opinions_opinionembedding SET court_id=%s "
                    f"WHERE court_id=%s AND opinion_id IN ({placeholders})",
                    [target_court_id, src_court_id, *chunk],
                )
