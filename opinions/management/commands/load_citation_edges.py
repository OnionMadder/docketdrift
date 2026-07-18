"""Load citation-graph edges (OpinionCitation) from a CL-derived
``citing_cluster_id,cited_cluster_id`` CSV.

Gives MN/AZ the "Cited by" + "Authorities cited" panels NH already has, built
from CourtListener's citation-map bulk export (search_opinionscited) -- no
eyecite, no text parsing. ``treatment`` defaults to CITED and there's no
``context_quote``: the bulk graph has only edges, so the quoted "How this
document has been cited" panel stays NH-only (that needs the text extractor).

Scoped to MN/AZ *citing* opinions so NH's richer text-extracted graph is never
touched. Idempotent: skips (citing, cited) pairs that already exist. Batched
with retry-reconnect; lifts the 25s cap.

Usage::

    python manage.py load_citation_edges --file mnaz_citation_edges.csv [--dry-run]
"""
from __future__ import annotations

import csv
import time
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from opinions.models import Court, Opinion, OpinionCitation

BATCH = 500  # citing cases per batch
DB_MAX_RETRIES = 5
DB_RETRY_SLEEP = 3


class Command(BaseCommand):
    help = "Load OpinionCitation edges from a citing_cluster,cited_cluster CSV (MN/AZ, idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="citing_cluster_id,cited_cluster_id CSV.")
        parser.add_argument("--dry-run", action="store_true", help="Count what would create; write nothing.")

    def handle(self, *args, file, dry_run, **opts):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        # We only create edges FROM MN/AZ opinions; NH keeps its text-extracted graph.
        scope_courts = set(
            Court.objects.filter(state__code__in=["MN", "AZ"]).values_list("id", flat=True)
        )

        by_citing = defaultdict(list)  # citing_cluster -> [cited_cluster, ...]
        try:
            with open(file, newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                next(reader, None)
                for row in reader:
                    if len(row) >= 2 and row[0] and row[1]:
                        by_citing[row[0]].append(row[1])
        except OSError as exc:
            raise CommandError(f"Cannot read {file!r}: {exc}")

        citing_clusters = list(by_citing)
        total = len(citing_clusters)
        edge_count = sum(len(v) for v in by_citing.values())
        self.stdout.write(f"Edges file: {total:,} citing cases, {edge_count:,} edges.")

        created = skipped_dup = 0
        for start in range(0, total, BATCH):
            chunk = citing_clusters[start:start + BATCH]
            for attempt in range(1, DB_MAX_RETRIES + 1):
                try:
                    # citing cluster -> our opinion id (MN/AZ only)
                    citing_map = {}
                    for o in (
                        Opinion.objects.filter(courtlistener_id__in=chunk)
                        .only("id", "courtlistener_id", "court_id")
                    ):
                        if o.court_id in scope_courts:
                            citing_map[o.courtlistener_id] = o.id
                    if not citing_map:
                        break

                    # cited clusters needed for the in-scope citing cases
                    cited_clusters = set()
                    for cc in chunk:
                        if cc in citing_map:
                            cited_clusters.update(by_citing[cc])
                    cited_map = {}  # cited cluster -> (op_id, reference)
                    if cited_clusters:
                        for o in (
                            Opinion.objects.filter(courtlistener_id__in=list(cited_clusters))
                            .only("id", "courtlistener_id", "reporter_cite", "case_number")
                        ):
                            cited_map[o.courtlistener_id] = (o.id, o.reporter_cite or o.case_number)

                    # existing edges for these citing opinions (idempotency)
                    citing_ids = list(citing_map.values())
                    existing = set(
                        OpinionCitation.objects.filter(citing_opinion_id__in=citing_ids)
                        .values_list("citing_opinion_id", "cited_opinion_id")
                    )

                    new_rows = []
                    for cc in chunk:
                        cop = citing_map.get(cc)
                        if cop is None:
                            continue
                        for tc in by_citing[cc]:
                            tgt = cited_map.get(tc)
                            if tgt is None:
                                continue
                            top, ref = tgt
                            if (cop, top) in existing:
                                skipped_dup += 1
                                continue
                            existing.add((cop, top))
                            new_rows.append(OpinionCitation(
                                citing_opinion_id=cop,
                                cited_opinion_id=top,
                                cited_reference=(ref or "")[:64],
                                treatment=OpinionCitation.Treatment.CITED,
                            ))
                    if new_rows and not dry_run:
                        OpinionCitation.objects.bulk_create(new_rows, batch_size=1000)
                    created += len(new_rows)
                    break
                except BaseException as exc:
                    if attempt >= DB_MAX_RETRIES:
                        raise
                    self.stderr.write(
                        f"  batch @{start} failed ({type(exc).__name__}: {exc}); "
                        f"reconnecting {attempt}/{DB_MAX_RETRIES}..."
                    )
                    try:
                        connection.close()
                    except BaseException:
                        pass
                    time.sleep(DB_RETRY_SLEEP)
            if start and start % (BATCH * 10) == 0:
                self.stdout.write(f"  citing processed={start:,}  created={created:,}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. edges created={created:,}  skipped_existing={skipped_dup:,}"
            + ("  (DRY RUN -- nothing written)" if dry_run else "")
        ))
