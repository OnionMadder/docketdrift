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
# PDF-header regex: LA COA opinions open with a two-column tabular block
# where the LEFT column carries the caption (parties + asterisk column
# separators) and the RIGHT column carries the court identity:
#   LORA JOHNSON              *      NO. 2025-CA-0560
#   VERSUS                    *      COURT OF APPEAL
#   CITY COUNCIL              *      FOURTH CIRCUIT
#                             *      STATE OF LOUISIANA
#
# pypdf extracts this row-by-row, so "COURT OF APPEAL" and the ordinal
# "FOURTH CIRCUIT" are separated by 30-80 chars of interleaved caption
# text ("CITY COUNCIL", the *  separator, etc). The first-pass regex
# required them adjacent and missed 81% of opinions on the first
# dry-run scan; the [\s\S]{0,150}? window covers up to ~2 caption lines
# between APPEAL and the ordinal.
#
# Non-greedy so the FIRST subsequent ordinal wins -- if a citation
# later in the head mentioned another circuit, it wouldn't be reached.
# ---------------------------------------------------------------------------
_HEADER_RE = re.compile(
    r"court\s+of\s+appeal[\s\S]{0,150}?"
    r"(first|second|third|fourth|fifth)\s+circuit",
    re.IGNORECASE,
)

# Fallback header regex: just the ordinal + "circuit", searched only in the
# first 800 chars so body-prose references (e.g. "the Third Circuit held...")
# can't false-match. Handles two failure modes the strict regex misses:
#   (a) First Circuit's own template SKIPS the "COURT OF APPEAL" line and
#       goes straight STATE OF LOUISIANA -> FIRST CIRCUIT (id=231046 shape).
#   (b) OCR occasionally drops the FIRST LETTER of the ordinal ("IRST
#       CIRCUIT" for id=400923). The optional-first-letter alternation
#       (f?irst|s?econd|t?hird|f?ourth|f?ifth) catches both intact and
#       broken forms; the stem lookup below maps either to the division.
# The ordinal + " circuit" pairing this early in the doc is distinctive
# to the header block (body citations use "La. App. N Cir." numeric form
# and don't appear before ~char 1500 in typical opinions).
_HEADER_FALLBACK_RE = re.compile(
    r"\b(f?irst|s?econd|t?hird|f?ourth|f?ifth)\s+circuit\b",
    re.IGNORECASE,
)
# Widened 800 -> 2000 after third dry-run found First Circuit writs (short
# "WRIT DENIED" dispositions) put the "COURT OF APPEAL, FIRST CIRCUIT"
# stamp in the signature block at the END of the doc, around offset 850-950.
# 2000 is still well below body-prose depth for typical opinions (~2500+).
_HEAD_WINDOW = 2000

_ORDINAL_TO_DIV = {
    "first":  "1",  "irst":  "1",
    "second": "2",  "econd": "2",
    "third":  "3",  "hird":  "3",
    "fourth": "4",  "ourth": "4",
    "fifth":  "5",  "ifth":  "5",
}


# ---------------------------------------------------------------------------
# JDC (Judicial District Court) -> COA circuit map. Third fallback after
# header regex + parish enumeration both fail.
#
# Louisiana has 43 numbered JDCs; 40 are single-circuit (all their parishes
# appeal to the same COA). Three are multi-circuit and MUST be skipped:
#   - 16th JDC (Iberia+St.Martin=3rd, St.Mary=1st)
#   - 23rd JDC (Ascension+Assumption=1st, St.James=5th)
#   - Orleans has "Civil District Court" (no JDC number) -- handled by
#     the separate CDC_ORLEANS_RE below, unambiguously 4th Cir.
# 41st and 43rd are not assigned (reserved).
#
# The In-Re/Appeal-From block that names the JDC is preserved through
# OCR far better than the parish name (numbers survive better than
# long multi-word proper nouns).
# ---------------------------------------------------------------------------
_JDC_TO_CIRCUIT: dict[str, str] = {
    "1": "2", "2": "2", "3": "2", "4": "2", "5": "2", "6": "2", "7": "2",
    "8": "2", "9": "3", "10": "3", "11": "2", "12": "3", "13": "3",
    "14": "3", "15": "3",
    # 16 SPLIT -- skip
    "17": "1", "18": "1", "19": "1", "20": "1", "21": "1", "22": "1",
    # 23 SPLIT -- skip
    "24": "5", "25": "4", "26": "2", "27": "3", "28": "3", "29": "5",
    "30": "3", "31": "3", "32": "1", "33": "3", "34": "4", "35": "3",
    "36": "3", "37": "2", "38": "3", "39": "2", "40": "5",
    # 41 unassigned
    "42": "2",
    # 43 unassigned
}

# "19th Judicial District Court" / "2 1st Judicial District" / "22nd JDC".
# The permissive digit-run pattern (\d+(?:\s+\d+)*) tolerates OCR
# broken-number cases where a two-digit number is split by whitespace
# ("2 1 st" was seen in id=455014's raw_text). Normalize by removing
# all whitespace before the map lookup.
_JDC_RE = re.compile(
    r"\b(\d+(?:\s+\d+)*)\s*(?:st|nd|rd|th)?\s+"
    r"judicial\s+district\s+court\b",
    re.IGNORECASE,
)

# Orleans Parish Civil District Court -> 4th Circuit (unambiguous).
_CDC_ORLEANS_RE = re.compile(
    r"\bcivil\s+district\s+court\b[\s\S]{0,80}?"
    r"(?:parish\s+of\s+orleans|orleans\s+parish)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parish caption match: LA appellate opinions carry either a
# parenthetical "(Parish of X)" or a caption phrase "district court,
# Parish of X, No. ..." or "district court for the parish of Orleans".
#
# The third dry-run's fuzzy regex kept mis-bounding multi-word parishes:
#   - "Parish of East Baton\nRouge, No..." captured only "East Baton"
#     (newline in the lookahead class closed the match too early).
#   - "PARISH OF BATON ROUGE" (all-caps caption at doc top) captured
#     "BATON ROUGE" -- but that isn't a parish; the actual parish is
#     East Baton Rouge or West Baton Rouge, and the regex had no way
#     to reach the "East"/"West" that appeared elsewhere in the doc.
#   - "Parish of St. Tammany - State of Louisiana" captured the whole
#     tail because " - " wasn't in the lookahead stop set.
#
# Cleaner: DIRECT ENUMERATED LOOKUP. Compile one pattern per parish name
# and try them longest-first (so "east baton rouge" matches before "east"
# would erroneously stop early). No fuzzy boundary detection needed --
# each pattern is anchored to the specific parish, and only exact-name
# hits count.
# ---------------------------------------------------------------------------


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

    Kept for reference / potential reuse. The enumerated-lookup path
    below doesn't need it, but a caller might want to canonicalize a
    parish name from another source.
    """
    s = (raw or "").strip().lower().rstrip(".,;: )")
    if s.startswith("the "):
        s = s[4:]
    s = re.sub(r"^saint\s+", "st. ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_parish_patterns() -> list[tuple[str, re.Pattern]]:
    """Compile 'parish of <NAME>' patterns for each of the 64 LA parishes.

    Sorted LONGEST parish name first so 'east baton rouge' matches
    before 'east' (there is no 'East' parish, but if the head text
    happens to be truncated to 'Parish of East' mid-caption, we want
    to return None rather than match a nonexistent shorter form).

    Whitespace inside the parish name matches flexibly: 'East    Baton\n
    Rouge' matches 'East Baton Rouge' because ' ' in the name becomes
    r'\s+' in the pattern. 'St.' escapes cleanly via re.escape.
    """
    out = []
    for name in sorted(_PARISH_TO_CIRCUIT, key=lambda n: -len(n)):
        pat_body = re.escape(name).replace(r"\ ", r"\s+")
        out.append((
            name,
            re.compile(r"parish\s+of\s+" + pat_body, re.IGNORECASE),
        ))
    return out


_PARISH_PATTERNS: list[tuple[str, re.Pattern]] = []  # lazy-init below


def detect_circuit(text: str) -> tuple[str | None, str]:
    """Return (division, source) for an LA COA opinion's raw_text.

    Returns:
        ("1".."5", "header")  when the PDF header names the circuit.
        ("1".."5", "parish")  when the caption parish maps to a circuit.
        ("1".."5", "jdc")     when a Judicial District Court number
                              (or Orleans Civil District Court) is found
                              and maps unambiguously to one circuit.
        (None,     "none")    when no signal is legible.
        (None,     "conflict") when header and parish disagree.
    """
    head = (text or "")[:2500]  # first ~2.5KB covers the caption block
    header_div: str | None = None
    parish_div: str | None = None
    parish_name = ""

    m = _HEADER_RE.search(head)
    if m:
        header_div = _ORDINAL_TO_DIV.get(m.group(1).lower())
    else:
        # Fallback: bare ordinal + "circuit" in a bounded head window.
        # See _HEADER_FALLBACK_RE docstring for why this is safe.
        fm = _HEADER_FALLBACK_RE.search(head[:_HEAD_WINDOW])
        if fm:
            header_div = _ORDINAL_TO_DIV.get(fm.group(1).lower())

    # Enumerated parish lookup (longest name wins so 'east baton rouge'
    # is tried before any of its sub-strings). ONLY runs when the header
    # didn't yield a signal -- 64 pattern searches per row × 141K rows
    # tripled the scan time in the fourth dry-run and got the process
    # NFSN-culled mid-run. Header signals ~45% of opinions; skipping
    # parish for those rows cuts parish work almost in half without
    # losing coverage (conflict detection was informational only; a
    # header hit is high-confidence on its own).
    jdc_div: str | None = None
    if header_div is None:
        global _PARISH_PATTERNS
        if not _PARISH_PATTERNS:
            _PARISH_PATTERNS = _build_parish_patterns()
        parish_head = head[:_HEAD_WINDOW]
        for name, pattern in _PARISH_PATTERNS:
            if pattern.search(parish_head):
                parish_div = _PARISH_TO_CIRCUIT[name]
                parish_name = name
                break

        # Third fallback: JDC number. Only try if BOTH header and parish
        # failed. Fast (one regex + one dict lookup).
        if parish_div is None:
            jdc_m = _JDC_RE.search(parish_head)
            if jdc_m:
                raw = jdc_m.group(1)
                num = "".join(raw.split())  # "2 1" -> "21"
                jdc_div = _JDC_TO_CIRCUIT.get(num)
            if jdc_div is None and _CDC_ORLEANS_RE.search(parish_head):
                jdc_div = "4"  # Civil District Court of Orleans

    if header_div and parish_div and header_div != parish_div:
        return None, "conflict"
    div = header_div or parish_div or jdc_div
    if div is None:
        return None, "none"
    src = "header" if header_div else ("parish" if parish_div else "jdc")
    return div, src


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
        parser.add_argument("--since", default=None,
                            help=(
                                "Only examine opinions with release_date >= "
                                "YYYY-MM-DD. This is what makes a post-ingest "
                                "run cheap: every CL ingest lands ALL five "
                                "circuits' opinions in the landing court "
                                "(First Circuit, the only one with a real CL "
                                "id), so circuits 2-5 stay frozen until this "
                                "command re-homes them. Chained into "
                                "cron-ingest.sh with a 30-day window it "
                                "examines a few dozen rows instead of "
                                "re-scanning 341K."
                            ))

    def handle(self, *args, apply, max_runtime, chunk_size, report_limit,
               since, **options):
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
        base_qs = Opinion.objects.filter(court_id__in=landing_ids)
        if since:
            base_qs = base_qs.filter(release_date__gte=since)
            self.stdout.write(f"  scoped to release_date >= {since}")
        qs = (
            base_qs
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
