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

import re
import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Court, Opinion, OpinionCitation, State
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
        parser.add_argument("--min-id", type=int, default=0,
                            help="Resume from this opinion id (see "
                                 "--max-runtime).")

    def handle(self, *args, state, limit, max_runtime, min_id, **options):
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
            # Resolution map: reporter_cite -> opinion_id for this state's corpus.
            cite_map = dict(
                Opinion.objects.filter(court_id__in=court_ids)
                .exclude(reporter_cite="")
                .values_list("reporter_cite", "id")
            )
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

            # Citing opinions: EVERY opinion with text. The old scoping
            # required a reporter_cite, which is an NH neutral-cite-era
            # assumption -- applied to MN it would skip every unpublished
            # opinion and all 3,102 backfilled ones, i.e. exactly the opinions
            # that can never get edges from CourtListener.
            ids = list(
                Opinion.objects.filter(court_id__in=court_ids)
                .filter(id__gte=min_id)
                .order_by("id")
                .values_list("id", flat=True)
            )
            if limit:
                ids = ids[:limit]
            self.stdout.write(
                "%s: %d resolvable dockets (%d ambiguous, dropped)"
                % (code, len(docket_owner), len(ambiguous))
            )
            # Start the clock AFTER the maps are built. Building them walks the
            # whole state corpus (~60s on MN), so counting it against
            # --max-runtime made the command exit having scanned zero opinions
            # while reporting success.
            started = time.time()
            self.stdout.write(
                "%s: scanning %d citing opinions (%d resolvable targets)..."
                % (code, len(ids), len(cite_map))
            )

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
                        for op in rows:
                            # Pass BOTH self keys. Without the docket, every MN
                            # opinion cites itself out of its own caption.
                            own = "%s|%s" % (op.reporter_cite or "",
                                             (op.case_number or "").strip())
                            cites = extract_citations(code, op.raw_text, self_cite=own)
                            OpinionCitation.objects.filter(citing_opinion_id=op.id).delete()
                            bulk = []
                            for c in cites:
                                target = cite_map.get(c.reporter_cite)
                                if target == op.id:
                                    continue  # never an edge to self
                                bulk.append(OpinionCitation(
                                    citing_opinion_id=op.id,
                                    cited_opinion_id=target,
                                    cited_reference=c.reporter_cite,
                                    treatment=classify_treatment(c.context),
                                    context=c.context[:500],
                                    context_quote=c.quote[:500],
                                    text_offset=c.text_offset,
                                ))
                                if target:
                                    internal += 1
                            if bulk:
                                OpinionCitation.objects.bulk_create(bulk)
                                edges += len(bulk)
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
