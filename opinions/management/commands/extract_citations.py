"""Build the citation graph: OpinionCitation edges from each opinion's body to
the other cases it cites, resolved against reporter_cite.

State-aware, idempotent (rebuilds each citing opinion's outgoing edges).
Batched with retry-and-reconnect, like extract_statutes / the reporter-cite
backfill -- a long-held cursor gets dropped by NFSN's MariaDB (2013).

Scoping: we only scan opinions that *have* a reporter_cite (NH's 2024+ neutral
era). A pre-2024 opinion can't cite a neutral cite (the cited opinion didn't
exist under the neutral system yet), so that set is exactly the opinions that
can participate in the neutral-cite graph -- which keeps this fast and light.

Usage::

    python manage.py extract_citations --state NH
    python manage.py extract_citations            # every live state w/ extractor
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from opinions.models import Court, Opinion, OpinionCitation, State
from opinions.parsing.citations import extract_citations
from opinions.parsing.treatment import classify_treatment

BATCH = 200
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP = 3


class Command(BaseCommand):
    help = "Extract the OpinionCitation graph (case-to-case citations) per state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state", default=None,
            help="USPS 2-letter code. Default: every live state.",
        )
        parser.add_argument("--limit", type=int, default=None,
                            help="Stop after N citing opinions (smoke test).")

    def handle(self, *args, state, limit, **options):
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
            # Citing opinions = those with a reporter_cite (the neutral-cite era).
            ids = list(
                Opinion.objects.filter(court_id__in=court_ids)
                .exclude(reporter_cite="")
                .values_list("id", flat=True)
            )
            if limit:
                ids = ids[:limit]
            self.stdout.write(
                "%s: scanning %d citing opinions (%d resolvable targets)..."
                % (code, len(ids), len(cite_map))
            )

            scanned = edges = internal = 0
            for start in range(0, len(ids), BATCH):
                chunk = ids[start:start + BATCH]
                for attempt in range(1, DB_MAX_RETRIES + 1):
                    try:
                        rows = list(
                            Opinion.objects.filter(id__in=chunk)
                            .only("id", "raw_text", "reporter_cite")
                        )
                        for op in rows:
                            cites = extract_citations(code, op.raw_text, self_cite=op.reporter_cite)
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
