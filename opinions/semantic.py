"""Semantic search helpers.

Wraps the Voyage query-side embedding + MariaDB ``VEC_DISTANCE_COSINE``
nearest-neighbor query into a small surface that views can call without
worrying about API mechanics or caching.

The flow:

1. ``get_query_embedding(query)`` -- returns the 1024-float vector for
   a search query. Caches per-query in ``QueryEmbedding`` so repeat
   searches cost zero Voyage credits.
2. ``search_similar_opinions(query_embedding, state, limit)`` -- runs
   the actual cosine-distance ORDER BY against the corpus, returning
   ordered Opinion IDs.

Voyage charges separately for "document" embedding (what we did to the
corpus) vs "query" embedding (what we do per search). The model
treats them asymmetrically -- mismatched input_type gives meaningless
similarity scores -- so make sure callers always use input_type='query'
for searches.

Local SQLite dev short-circuits: the embedding column doesn't exist
there, so semantic search returns ``[]`` silently and the view falls
back to keyword-only.
"""
from __future__ import annotations

import json
import logging
import os

import requests
from django.db import connection

from opinions.models import QueryEmbedding

logger = logging.getLogger(__name__)


VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-law-2"
VOYAGE_TIMEOUT_SECONDS = 30  # Query embedding is one short doc, fast.
QUERY_LENGTH_CAP = 255       # Skip cache for queries longer than this (matches QueryEmbedding.query column).


def _run_vector_query(sql: str, params) -> list:
    """Execute a cosine-distance SELECT, returning rows -- or [] on failure.

    The ``VEC_DISTANCE_COSINE`` scans here are O(N) over the embedding
    column (no VECTOR INDEX until the NOT-NULL migration). On a dense
    state corpus a single scan can exceed the 25s ``max_statement_time``
    set in settings; MariaDB then KILLs the query, which not only raises
    but leaves the *pooled* connection in an interrupted state. The next
    request that reuses that connection hits errno 188 / 1317 ("Operation
    was interrupted" / "Query execution was interrupted") on whatever it
    runs next and 500s -- the connection-poison cascade documented in
    CLAUDE.md that takes pages down site-wide, not just the slow one.

    So: catch ANY DatabaseError, drop the poisoned connection (Django
    transparently reopens a clean one on next use), and return [] so the
    caller degrades gracefully -- no similar-opinions widget / keyword-only
    search -- instead of bubbling a 500 and poisoning the pool.
    """
    from django.db import DatabaseError

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    except DatabaseError as exc:
        logger.warning("vector query failed (%s); dropping connection", exc)
        try:
            connection.close()
        except Exception:
            pass
        return []


def get_query_embedding(query: str) -> list[float] | None:
    """Return the voyage-law-2 query embedding for ``query``, or None.

    Returns None when:
    - VOYAGE_API_KEY is not configured (local dev without secrets)
    - Voyage API call fails
    - Query is empty after normalization

    Cached per normalized query string. Cache hit increments ``hit_count``
    and refreshes ``last_used_at`` so we can LRU-evict later if needed.
    """
    normalized = (query or "").strip().lower()
    if not normalized:
        return None

    # Cache hit path -- check first, no API call.
    if len(normalized) <= QUERY_LENGTH_CAP:
        cached = QueryEmbedding.objects.filter(query=normalized).first()
        if cached is not None:
            QueryEmbedding.objects.filter(pk=cached.pk).update(
                hit_count=cached.hit_count + 1,
            )
            try:
                return json.loads(cached.embedding_json)
            except (TypeError, ValueError):
                logger.warning("Corrupt cache entry for %r; refetching.", normalized)
                cached.delete()

    # Miss -- call Voyage.
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        return None  # Local dev without a key; fall back to keyword.

    try:
        response = requests.post(
            VOYAGE_EMBED_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": [normalized],
                "model": VOYAGE_MODEL,
                "input_type": "query",  # NOT 'document' -- asymmetric matters.
                "truncation": True,
            },
            timeout=VOYAGE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        embedding = payload["data"][0]["embedding"]
    except Exception as exc:
        logger.warning("Voyage query embed failed for %r: %s", normalized, exc)
        return None

    # Persist to cache (no harm if a race already inserted; uniqueness is
    # PK on query so update_or_create avoids the rare collision).
    if len(normalized) <= QUERY_LENGTH_CAP:
        QueryEmbedding.objects.update_or_create(
            query=normalized,
            defaults={"embedding_json": json.dumps(embedding)},
        )

    return embedding


def search_similar_opinions(
    query_embedding: list[float],
    state,
    limit: int = 10,
    date_cutoff=None,
) -> list[int]:
    """Return top-N Opinion IDs by cosine distance, ordered nearest first.

    Empty list when:
    - We're not on MariaDB (local SQLite dev has no VECTOR column)
    - ``query_embedding`` is falsy
    - No state given (we always state-scope semantic search)

    ``date_cutoff`` (a ``datetime.date``) filters to opinions filed on
    or after that date -- used to match the keyword/FULLTEXT search's
    default 10-year window so the two surfaces never disagree.

    Uses ``VEC_DISTANCE_COSINE`` against ``Opinion.embedding``. No
    HNSW index (see migration 0015 docstring); at 60K rows a full
    scan completes in ~30-80ms which is fine for current scale.
    """
    if connection.vendor != "mysql":
        return []
    if not query_embedding or state is None:
        return []

    query_vec_text = json.dumps(query_embedding)

    sql = [
        "SELECT o.id,",
        "       VEC_DISTANCE_COSINE(o.embedding, Vec_FromText(%s)) AS dist",
        "FROM opinions_opinion o",
        "JOIN opinions_court c ON c.id = o.court_id",
        "WHERE c.state_id = %s",
        "  AND o.embedding IS NOT NULL",
    ]
    params = [query_vec_text, state.code]
    if date_cutoff is not None:
        sql.append("  AND o.release_date >= %s")
        params.append(date_cutoff)
    sql.append("ORDER BY dist")
    sql.append("LIMIT %s")
    params.append(limit)

    rows = _run_vector_query("\n".join(sql), params)
    return [row[0] for row in rows]


def similar_to_opinion(opinion, limit: int = 5, with_scores: bool = False):
    """Return opinion IDs most similar to ``opinion``, excluding itself.

    Used by the "Similar opinions" widget on detail pages. Doesn't touch
    Voyage at all -- we already have ``opinion.embedding`` stored, so this
    is a pure DB-side cosine-distance lookup.

    With ``with_scores=False`` (default, unchanged contract) returns a
    ``list[int]`` of opinion IDs nearest first. With ``with_scores=True``
    returns a ``list[tuple[int, float]]`` of ``(opinion_id, cosine_distance)``
    in the same order -- the caller turns the distance into a "% similar"
    quality cue. The underlying query is identical either way; the flag
    only controls whether the already-selected ``dist`` column is surfaced.

    Performance gate: this query is an O(N) full scan over the state's
    opinion embeddings because MariaDB's VECTOR INDEX requires NOT NULL
    and our embedding column allows null until the embedding backfill
    finishes. At 60K MN rows the scan was ~500ms-2s; after NH+AZ landed
    (tripling the live-state corpus) some scans cross 20s, which then
    saturates the single gunicorn worker. Until we backfill all
    embeddings and migrate the column to NOT NULL + index, this widget
    is gated on a date_cutoff that limits the scan to recent opinions.
    """
    if connection.vendor != "mysql":
        return []
    if not opinion or not opinion.court_id:
        return []

    # Limit the candidate set to the trailing ~3 years of the opinion's
    # state corpus -- still gives a useful similar-opinions surface for
    # 95%+ of pages, and keeps the scan footprint bounded as the corpus
    # grows. Subqueries are kept simple so the optimizer picks the
    # release_date btree index first.
    from datetime import timedelta
    date_cutoff = None
    if opinion.release_date is not None:
        date_cutoff = opinion.release_date - timedelta(days=3 * 365)

    sql = [
        "SELECT o.id,",
        "       VEC_DISTANCE_COSINE(o.embedding, src.embedding) AS dist",
        "FROM opinions_opinion o",
        "JOIN opinions_court c ON c.id = o.court_id",
        "JOIN opinions_opinion src ON src.id = %s",
        "WHERE c.state_id = (SELECT state_id FROM opinions_court WHERE id = %s)",
        "  AND o.id != %s",
        "  AND o.embedding IS NOT NULL",
        "  AND src.embedding IS NOT NULL",
    ]
    params: list = [opinion.id, opinion.court_id, opinion.id]
    if date_cutoff is not None:
        sql.append("  AND o.release_date >= %s")
        params.append(date_cutoff)
    sql.append("ORDER BY dist")
    sql.append("LIMIT %s")
    params.append(limit)

    rows = _run_vector_query("\n".join(sql), params)
    if with_scores:
        return [(row[0], row[1]) for row in rows]
    return [row[0] for row in rows]
