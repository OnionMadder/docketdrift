"""Cluster near-identical citing passages so the public panel can collapse
them into Scholar-style "... and N similar citations" rows.

Third step of the "How this document has been cited" pipeline:

    extract_citations  ->  embed_citations  ->  cluster_citations

For each CITED opinion (a case in our corpus), we take all the internal edges
pointing at it -- i.e. every later opinion that cites it -- and greedily group
their ``context_embedding`` vectors by cosine similarity. Each group gets a
``cluster_label`` (unique within that cited opinion) and one
``is_cluster_lead`` edge: the representative quote shown on the page, with the
other members counted as "and N similar citations".

Pure-Python cosine -- numpy is unusable on NFSN's FreeBSD (see CLAUDE.md). The
per-opinion edge count is small, so O(k^2) is trivial.

Idempotent: every run fully recomputes a cited opinion's clustering, so re-run
freely after extract_citations + embed_citations. Edges with no embedding each
become their own singleton cluster (lead iff they carry a quote), so they still
appear -- just never merged.

Usage::

    python manage.py cluster_citations --state NH
    python manage.py cluster_citations --state NH --threshold 0.9
"""
from __future__ import annotations

import math
import time

from django.core.management.base import BaseCommand
from django.db import connection

# Cosine-similarity floor for two citing passages to count as "the same
# proposition". voyage-law-2 puts paraphrases of one holding very high
# (>0.92) and distinct propositions noticeably lower, so a high default
# avoids wrongly merging different reasons-for-citing. Tunable per run.
DEFAULT_THRESHOLD = 0.88

DB_MAX_RETRIES = 5
DB_RETRY_SLEEP = 5


def _norm(vec):
    return math.sqrt(sum(x * x for x in vec))


def _cosine(a, b, na, nb):
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _cluster(edges, threshold):
    """Greedy single-linkage-to-seed clustering.

    ``edges`` is a list of (id, vector_or_None, quote). Returns
    ``{edge_id: (cluster_label, is_lead)}``. Embedded edges merge by cosine;
    un-embedded edges become singletons (lead iff they have a quote).
    """
    assignment = {}
    label = 0

    embedded = [(eid, vec, q, _norm(vec)) for (eid, vec, q) in edges if vec]
    unembedded = [(eid, None, q, 0.0) for (eid, vec, q) in edges if not vec]

    assigned = set()
    for i, (eid, vec, q, nrm) in enumerate(embedded):
        if eid in assigned:
            continue
        members = [(eid, q)]
        assigned.add(eid)
        for j in range(i + 1, len(embedded)):
            ojd, ovec, oq, onrm = embedded[j]
            if ojd in assigned:
                continue
            if _cosine(vec, ovec, nrm, onrm) >= threshold:
                members.append((ojd, oq))
                assigned.add(ojd)
        # Lead = the longest quote in the cluster (most complete passage).
        lead_id = max(members, key=lambda m: len(m[1] or ""))[0]
        for mid, _q in members:
            assignment[mid] = (label, mid == lead_id)
        label += 1

    # Each un-embedded edge is its own cluster; it leads only if it can be
    # shown (has a quote), otherwise it's a right-column-only citation.
    for eid, _vec, q, _nrm in unembedded:
        assignment[eid] = (label, bool(q))
        label += 1

    return assignment


class Command(BaseCommand):
    help = "Cluster similar citing passages per cited opinion (for 'N similar citations')."

    def add_arguments(self, parser):
        parser.add_argument("--state", default=None,
                            help="USPS 2-letter code (e.g. NH). Default: all live states.")
        parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                            help="Cosine floor to merge passages (default %.2f)." % DEFAULT_THRESHOLD)

    def handle(self, *args, state, threshold, **options):
        from opinions.models import Court, OpinionCitation, State

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
            cited_ids = list(
                OpinionCitation.objects
                .filter(cited_opinion__court_id__in=court_ids)
                .values_list("cited_opinion_id", flat=True)
                .distinct()
            )
            if not cited_ids:
                self.stdout.write("%s: no internal citations to cluster." % code)
                continue

            self.stdout.write(self.style.SUCCESS(
                "%s: clustering incoming citations for %d cited opinions "
                "(threshold %.2f)." % (code, len(cited_ids), threshold)
            ))

            opinions_done = clusters_total = merges = 0
            for cited_id in cited_ids:
                rows = list(
                    OpinionCitation.objects.filter(cited_opinion_id=cited_id)
                    .only("id", "context_embedding", "context_quote")
                )
                edges = [(r.id, r.context_embedding, r.context_quote) for r in rows]
                assignment = _cluster(edges, threshold)

                changed = []
                for r in rows:
                    new_label, new_lead = assignment[r.id]
                    if r.cluster_label != new_label or r.is_cluster_lead != new_lead:
                        r.cluster_label = new_label
                        r.is_cluster_lead = new_lead
                        changed.append(r)
                if changed:
                    self._bulk_update(changed)

                n_clusters = len({lbl for lbl, _ in assignment.values()})
                clusters_total += n_clusters
                merges += len(edges) - n_clusters
                opinions_done += 1
                if opinions_done % 200 == 0:
                    self.stdout.write("  ...%d/%d cited opinions"
                                      % (opinions_done, len(cited_ids)))

            self.stdout.write(self.style.SUCCESS(
                "%s done. cited_opinions=%d clusters=%d merged_away=%d"
                % (code, opinions_done, clusters_total, merges)
            ))

    def _bulk_update(self, objs):
        """bulk_update with retry-and-reconnect on NFSN's SSL drops."""
        from opinions.models import OpinionCitation
        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                OpinionCitation.objects.bulk_update(
                    objs, ["cluster_label", "is_cluster_lead"]
                )
                return
            except BaseException as exc:
                if attempt >= DB_MAX_RETRIES:
                    raise
                self.stderr.write(
                    "  DB error (attempt %d/%d) %s; reconnecting..."
                    % (attempt, DB_MAX_RETRIES, type(exc).__name__)
                )
                try:
                    connection.close()
                except BaseException:
                    pass
                time.sleep(DB_RETRY_SLEEP)
