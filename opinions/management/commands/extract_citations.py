"""Build the citation graph: OpinionCitation edges from each opinion's body to
the other cases it cites, resolved against reporter_cite.

State-aware, idempotent (rebuilds each citing opinion's outgoing edges).
Batched with retry-and-reconnect, like extract_statutes / the reporter-cite
backfill -- a long-held cursor gets dropped by NFSN's MariaDB (2013).

Scoping: EVERY opinion with text is a candidate citing opinion. This used to
require a reporter_cite, which was an NH neutral-cite-era assumption (a
pre-2024 NH opinion cannot cite a neutral cite, so the restriction was free
there). Applied to Minnesota that same rule silently skips every unpublished
opinion and the entire 2020-2022 backfill -- precisely the opinions that can
never receive edges from CourtListener's bulk map.

Resolution is by reporter_cite AND by canonical docket number. Ambiguous
dockets (one docket, two opinions -- a case keeps its number through review)
are dropped rather than guessed.

Usage::

    python manage.py extract_citations --state NH
    python manage.py extract_citations --state MN --max-runtime 35
    python manage.py extract_citations            # every live state w/ extractor
"""
from __future__ import annotations

import json
import os
import re
import time

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from opinions.models import (Court, Opinion, OpinionCitation, ParallelCite,
                             State)
from opinions.parsing.citations import extract_citations
from opinions.parsing.treatment import classify_treatment

BATCH = 200
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP = 3

# Legacy rows carry 'NO. ' prefixes, unpadded sequences (A15-178), and
# filename stems (a230380) -- see normalize_case_numbers. Comparison has to
# canonicalize or a docket cite never matches its target.
_DOCKET_RE = re.compile(r"^A(\d{2})-?(\d{1,4})$")


def _canonical_docket(case_number: str) -> str:
    s = (case_number or "").strip().upper().replace("NO.", "").replace(" ", "")
    m = _DOCKET_RE.match(s)
    return "A%s-%s" % (m.group(1), m.group(2).zfill(4)) if m else ""


class Command(BaseCommand):
    help = "Extract the OpinionCitation graph (case-to-case citations) per state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state", default=None,
            help="USPS 2-letter code. Default: every live state.",
        )
        parser.add_argument("--limit", type=int, default=None,
                            help="Stop after N citing opinions (smoke test).")
        parser.add_argument("--max-runtime", type=int, default=0,
                            help="Self-exit after N seconds and print a "
                                 "--min-id to resume from. NFSN's CPU cull is "
                                 "~40s, so use 35 for a full MN sweep.")
        parser.add_argument(
            "--map-cache", default=None,
            help="Path to a JSON cache of the resolution map. Building the "
                 "map walks the whole state corpus (~5.5 min on LA, measured "
                 "2026-10-05), which is longer than NFSN's cull, so a chunked "
                 "sweep could never get past it. With this flag the first "
                 "chunk builds and saves the map; later chunks load it in "
                 "about a second. Rebuilt when older than --map-cache-hours.")
        parser.add_argument("--map-cache-hours", type=float, default=24.0)
        parser.add_argument(
            "--resolve-only", action="store_true",
            help="Do not re-read opinion text. Re-resolve EXISTING unresolved "
                 "extracted edges against the current map (reporter cites, "
                 "parallel cites, dockets). For when the map grew after "
                 "extraction -- LA's graph was extracted before its cites "
                 "were loaded and resolved nothing. One batched UPDATE per "
                 "batch instead of delete + re-insert through 7 indexes.")
        parser.add_argument("--min-id", type=int, default=0,
                            help="Resume from this opinion id (see "
                                 "--max-runtime).")

    def handle(self, *args, state, limit, max_runtime, min_id, map_cache=None,
               map_cache_hours=24.0, resolve_only=False, **options):
        started = time.time()
        # Batch work, not a web request: the 25s cap from settings would kill
        # the corpus-wide map builds below.
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")
        if state:
            codes = [state.upper()]
        else:
            codes = list(State.objects.filter(is_live=True).values_list("code", flat=True))

        for code in codes:
            court_ids = list(
                Court.objects.filter(state__code=code).values_list("id", flat=True)
            )
            if not court_ids:
                continue
            cache_path = None
            if map_cache:
                cache_path = map_cache if len(codes) == 1 else "%s.%s" % (map_cache, code)
            cite_map = self._load_map_cache(cache_path, map_cache_hours)
            if cite_map is not None:
                self.stdout.write("%s: loaded %d resolvable keys from %s"
                                  % (code, len(cite_map), cache_path))
            else:
                cite_map = self._build_map(court_ids, code)
                if cache_path:
                    self._save_map_cache(cache_path, cite_map)

            # Citing opinions: EVERY opinion with text. The old scoping
            # required a reporter_cite, which is an NH neutral-cite-era
            # assumption -- applied to MN it would skip every unpublished
            # opinion and all 3,102 backfilled ones, i.e. exactly the opinions
            # that can never get edges from CourtListener.
            # The id list is cached beside the map for the same reason: on LA
            # this query takes 155s (id >= N + ORDER BY id beside a court_id
            # filter flips to a PRIMARY walk of the 2.75GB table -- the
            # documented "one non-covered column" trap), while extraction
            # itself runs ~400 opinions/s.
            ids_path = cache_path + ".ids" if cache_path else None
            all_ids = self._load_map_cache(ids_path, map_cache_hours)
            if all_ids is None:
                all_ids = list(
                    Opinion.objects.filter(court_id__in=court_ids)
                    .order_by("id")
                    .values_list("id", flat=True)
                )
                if ids_path:
                    self._save_map_cache(ids_path, all_ids)
            ids = [i for i in all_ids if i >= min_id]
            if limit:
                ids = ids[:limit]
            # Start the clock AFTER the maps are built. Building them walks the
            # whole state corpus (~60s on MN), so counting it against
            # --max-runtime made the command exit having scanned zero opinions
            # while reporting success.
            started = time.time()
            self.stdout.write(
                "%s: scanning %d citing opinions (%d resolvable targets)..."
                % (code, len(ids), len(cite_map))
            )
            if resolve_only:
                self._resolve(code, ids, cite_map, max_runtime, started)
            else:
                self._sweep(code, ids, cite_map, max_runtime, started)

    def _resolve(self, code, ids, cite_map, max_runtime, started):
        """Fill cited_opinion on existing extracted edges; no text re-read.

        Reproduces the extractor's rules exactly, so the result equals a
        full re-extraction with the current map: rows were inserted in cite
        order, so walking a citing opinion's rows by id visits its cites in
        text order; the FIRST row reaching a target keeps it, a later row
        reaching the same target is dropped (the extractor writes one edge
        per resolved target -- parallel-cite pairs), and a row resolving to
        the citing opinion itself is dropped (never an edge to self).
        Rows that still resolve to nothing are left as external authority.
        """
        scanned = resolved = dropped = 0
        stopped_at = 0
        for start in range(0, len(ids), BATCH):
            if max_runtime and (time.time() - started) > max_runtime:
                stopped_at = ids[start]
                self.stdout.write(self.style.WARNING(
                    "  time budget hit; resume with:  --min-id %d" % stopped_at))
                break
            chunk = ids[start:start + BATCH]
            for attempt in range(1, DB_MAX_RETRIES + 1):
                try:
                    edges = list(
                        OpinionCitation.objects.filter(
                            citing_opinion_id__in=chunk,
                            source=OpinionCitation.Source.EXTRACTED,
                        ).order_by("id").values_list(
                            "id", "citing_opinion_id", "cited_opinion_id",
                            "cited_reference")
                    )
                    seen: dict[int, set] = {}
                    for _id, citing, cited, _ref in edges:
                        if cited is not None:
                            seen.setdefault(citing, set()).add(cited)
                    updates: dict[int, int] = {}
                    drop: list[int] = []
                    for _id, citing, cited, ref in edges:
                        if cited is not None:
                            continue
                        target = cite_map.get(ref)
                        if target is None:
                            continue
                        mine = seen.setdefault(citing, set())
                        if target == citing or target in mine:
                            drop.append(_id)
                        else:
                            mine.add(target)
                            updates[_id] = target
                    with transaction.atomic():
                        if updates:
                            case = " ".join("WHEN %d THEN %d" % (k, v)
                                            for k, v in updates.items())
                            with connection.cursor() as cur:
                                cur.execute(
                                    "UPDATE opinions_opinioncitation "
                                    "SET cited_opinion_id = CASE id %s END "
                                    "WHERE id IN (%s)"
                                    % (case, ",".join(str(k) for k in updates)))
                        if drop:
                            OpinionCitation.objects.filter(id__in=drop).delete()
                    resolved += len(updates)
                    dropped += len(drop)
                    scanned += len(chunk)
                    break
                except BaseException as exc:
                    if attempt >= DB_MAX_RETRIES:
                        raise
                    self.stderr.write(
                        "  batch @%d failed (%s); reconnect %d/%d"
                        % (start, type(exc).__name__, attempt, DB_MAX_RETRIES))
                    try:
                        connection.close()
                    except BaseException:
                        pass
                    time.sleep(DB_RETRY_SLEEP)
        self.stdout.write(self.style.SUCCESS(
            "%s resolve done. scanned=%d resolved=%d dropped=%d"
            % (code, scanned, resolved, dropped)))

    @staticmethod
    def _load_map_cache(path, max_hours):
        if not path or not os.path.exists(path):
            return None
        if time.time() - os.path.getmtime(path) > max_hours * 3600:
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def _save_map_cache(path, cite_map):
        # Write-then-rename, so a chunk killed mid-write never leaves a
        # truncated cache that the next chunk would trust.
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cite_map, fh)
        os.replace(tmp, path)

    def _build_map(self, court_ids, code):
            # Resolution map: reporter_cite -> opinion_id for this state's corpus.
            cite_map = dict(
                Opinion.objects.filter(court_id__in=court_ids)
                .exclude(reporter_cite="")
                .values_list("reporter_cite", "id")
            )
            # ...plus every PARALLEL cite. reporter_cite holds one canonical
            # form per opinion, but courts cite the same case several ways --
            # Arizona by official "221 Ariz. 236" far more often than by the
            # Pacific "202 P.3d 1150" we happened to store, Minnesota by
            # "123 Minn. 456" alongside N.W.2d. Without these, the most common
            # citation format in an AZ opinion resolves to nothing.
            # setdefault: never let a parallel cite displace a canonical one.
            for cite, oid in ParallelCite.objects.filter(
                    opinion__court_id__in=court_ids).values_list("cite", "opinion_id"):
                if cite:
                    cite_map.setdefault(cite.strip(), oid)

            # ...plus canonical DOCKET -> opinion_id. A docket is the only key
            # that reaches an opinion with no reporter cite -- every
            # unpublished opinion, and the whole MN 2020-2022 backfill, which
            # CourtListener has no data for. Ambiguous dockets are DROPPED, not
            # guessed: a docket follows a case through review, so ~1,292 MN
            # dockets carry both a COA and a Supreme opinion and there is no
            # sound way to tell which one a bare docket cite meant.
            docket_owner: dict[str, int] = {}
            ambiguous: set[str] = set()
            for cn, oid in Opinion.objects.filter(
                    court_id__in=court_ids).values_list("case_number", "id"):
                canon = _canonical_docket(cn)
                if not canon:
                    continue
                if canon in docket_owner and docket_owner[canon] != oid:
                    ambiguous.add(canon)
                else:
                    docket_owner[canon] = oid
            for k in ambiguous:
                docket_owner.pop(k, None)
            for k, v in docket_owner.items():
                cite_map.setdefault(k, v)
            self.stdout.write(
                "%s: %d resolvable dockets (%d ambiguous, dropped)"
                % (code, len(docket_owner), len(ambiguous))
            )
            return cite_map

    def _sweep(self, code, ids, cite_map, max_runtime, started):
            scanned = edges = internal = 0
            stopped_at = 0
            for start in range(0, len(ids), BATCH):
                if max_runtime and (time.time() - started) > max_runtime:
                    stopped_at = ids[start]
                    self.stdout.write(self.style.WARNING(
                        "  time budget hit; resume with:  --min-id %d" % stopped_at))
                    break
                chunk = ids[start:start + BATCH]
                for attempt in range(1, DB_MAX_RETRIES + 1):
                    try:
                        rows = list(
                            Opinion.objects.filter(id__in=chunk)
                            .only("id", "raw_text", "reporter_cite", "case_number")
                        )
                        bulk = []
                        batch_internal = 0
                        for op in rows:
                            # Pass BOTH self keys. Without the docket, every MN
                            # opinion cites itself out of its own caption.
                            own = "%s|%s" % (op.reporter_cite or "",
                                             (op.case_number or "").strip())
                            cites = extract_citations(code, op.raw_text, self_cite=own)
                            # One edge per RESOLVED TARGET, not per cite
                            # string. Courts routinely give both cites for one
                            # case in a single reference -- "State v. Doe, 221
                            # Ariz. 236, 202 P.3d 1150" -- which are two
                            # different keys pointing at the same opinion. Now
                            # that parallel cites resolve, counting those
                            # separately would double every Arizona edge and
                            # show the same case twice in "cited by".
                            # Unresolved references keep their own rows: they
                            # are genuinely distinct external authorities.
                            seen_targets: set[int] = set()
                            for c in cites:
                                target = cite_map.get(c.reporter_cite)
                                if target == op.id:
                                    continue  # never an edge to self
                                if target is not None:
                                    if target in seen_targets:
                                        continue
                                    seen_targets.add(target)
                                    batch_internal += 1
                                bulk.append(OpinionCitation(
                                    citing_opinion_id=op.id,
                                    cited_opinion_id=target,
                                    cited_reference=c.reporter_cite,
                                    treatment=classify_treatment(c.context),
                                    context=c.context[:500],
                                    context_quote=c.quote[:500],
                                    text_offset=c.text_offset,
                                    source=OpinionCitation.Source.EXTRACTED,
                                ))
                        # Rebuild only OUR OWN edges. Deleting everything
                        # would wipe CourtListener's bulk map (335,998 MN
                        # edges), which resolves against their full corpus
                        # and reaches cases we don't hold -- our regex
                        # cannot reproduce those.
                        #
                        # ONE delete + ONE insert per batch, in a transaction.
                        # Per-opinion delete/insert was ~3 round-trips per
                        # opinion (~6 opinions/s on LA, a 16-hour sweep), and
                        # a kill between an opinion's delete and its insert
                        # silently lost its edges (the --force trap). Now a
                        # killed batch rolls back whole.
                        with transaction.atomic():
                            OpinionCitation.objects.filter(
                                citing_opinion_id__in=[op.id for op in rows],
                                source=OpinionCitation.Source.EXTRACTED,
                            ).delete()
                            if bulk:
                                OpinionCitation.objects.bulk_create(bulk, batch_size=500)
                        edges += len(bulk)
                        internal += batch_internal
                        scanned += len(rows)
                        break
                    except BaseException as exc:
                        if attempt >= DB_MAX_RETRIES:
                            raise
                        self.stderr.write(
                            "  batch @%d failed (%s); reconnect %d/%d"
                            % (start, type(exc).__name__, attempt, DB_MAX_RETRIES)
                        )
                        try:
                            connection.close()
                        except BaseException:
                            pass
                        time.sleep(DB_RETRY_SLEEP)

            self.stdout.write(self.style.SUCCESS(
                "%s done. scanned=%d edges=%d (internal=%d external=%d)"
                % (code, scanned, edges, internal, edges - internal)
            ))
