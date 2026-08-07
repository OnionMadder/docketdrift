"""Semantic search helpers.

Wraps the Voyage query-side embedding + MariaDB ``VEC_DISTANCE_COSINE``
nearest-neighbor query into a small surface that views can call without
worrying about API mechanics or caching.

The flow:

1. ``get_query_embedding(query)`` -- returns the 1024-float vector for
   a search query. Caches per-query in a PROCESS-LOCAL LRU so repeat
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


logger = logging.getLogger(__name__)


VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"
VOYAGE_MODEL = "voyage-law-2"
VOYAGE_TIMEOUT_SECONDS = 30  # Query embedding is one short doc, fast.
QUERY_LENGTH_CAP = 255       # Skip cache for queries longer than this.
# Process-local query-embedding cache. DELIBERATELY in-memory, never on disk:
# a durable query->embedding table is a log of what users searched — the exact
# subpoenable research-trail artifact the Privacy page promises cannot exist
# ("we cannot produce what we never stored"). With workers=1 a dict still
# deduplicates repeat searches across ALL users for the worker's lifetime
# (~75-90 min between recycles); a cache miss costs one Voyage call
# (~$0.000001). Bots never reach this path (request_is_crawler gate).
_EMBED_CACHE: dict[str, list] = {}
_EMBED_CACHE_MAX = 512

# Per-query wall-clock bound for the cosine scans, well under the 25s session
# max_statement_time. A healthy scan (NH ~215ms, warm MN/AZ) finishes far
# inside this; a pathological cold scan on a dense corpus is KILLed here and
# the caller degrades to [] -- so the single gunicorn worker never blocks the
# full 25s on one similar-opinions widget. Remove once the VECTOR INDEX lands.
VECTOR_QUERY_TIMEOUT_S = 12


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

    So: catch the failure, drop the poisoned connection (Django transparently
    reopens a clean one on next use), and return [] so the caller degrades
    gracefully -- no similar-opinions widget / keyword-only search -- instead
    of bubbling a 500 and poisoning the pool.

    We catch ``Exception``, not just ``django.db.DatabaseError``: the
    max_statement_time KILL frequently lands while reading result rows
    (``fetchall``), which Django does NOT route through its error-translation
    layer, so it surfaces as a RAW ``pymysql.err.OperationalError`` that is
    not a subclass of DatabaseError. Catching Exception covers both the
    django-wrapped (execute-time) and raw-pymysql (fetch-time) forms while
    still letting KeyboardInterrupt/SystemExit (BaseException) propagate.

    The scan is wrapped in ``SET STATEMENT max_statement_time=N FOR ...`` so it
    self-bounds to VECTOR_QUERY_TIMEOUT_S (well under the 25s session cap),
    keeping the single worker from stalling the full 25s on a cold dense scan.
    """
    bounded_sql = "SET STATEMENT max_statement_time=%d FOR %s" % (
        VECTOR_QUERY_TIMEOUT_S,
        sql,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(bounded_sql, params)
            return cursor.fetchall()
    except Exception as exc:
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

    # Cache hit path -- process-local, no API call, nothing persisted.
    if normalized in _EMBED_CACHE:
        return _EMBED_CACHE[normalized]

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

    if len(normalized) <= QUERY_LENGTH_CAP:
        if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
            # Cheap eviction: drop the oldest insertion (dicts are ordered).
            _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)))
        _EMBED_CACHE[normalized] = embedding

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

    # Scan the SLIM table (opinions_opinionembedding), not the fat one.
    # Measured 2026-08-05: the fat-table scan drags 2.75GB of clustered rows
    # through the 8MB buffer pool (~2,400 rows/s -- MN needs ~29s vs the 12s
    # bound, killed every time). The slim table is dense, and its clustered
    # PK (court_id, release_date, opinion_id) means a date-windowed scan
    # reads ONLY the window's pages: cold, MN 10yr = 2.2s / AZ 2.8s / NH
    # 0.8s. The view's default 10-year window keeps this on the fast path;
    # an explicit years=all full scan completes on NH (~8s) and degrades to
    # [] at the bound on MN/AZ until the corpus gets real vector infra.
    # Only embedded rows enter the slim table, so no embedding_pending
    # predicate is needed here.
    court_ids = list(state.courts.values_list("id", flat=True))
    if not court_ids:
        return []
    placeholders = ",".join(["%s"] * len(court_ids))
    sql = [
        "SELECT e.opinion_id,",
        "       VEC_DISTANCE_COSINE(e.embedding, Vec_FromText(%s)) AS dist",
        "FROM opinions_opinionembedding e",
        "WHERE e.court_id IN (" + placeholders + ")",
    ]
    params = [query_vec_text, *court_ids]
    if date_cutoff is not None:
        sql.append("  AND e.release_date >= %s")
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
    date_cutoff = date_ceiling = None
    if opinion.release_date is not None:
        # SYMMETRIC +/-3y window. The lower bound alone (the original shape)
        # left old opinions with an unbounded top: a 2004 MN opinion scanned
        # 2001->today -- ~30K slim rows after the 2026-08 backfills, blowing
        # the 12s bound and burning a worker thread on every cold page
        # (measured 2026-08-07: 12.0s -> KILLed -> widget silently empty).
        # The ceiling makes every page's scan the same bounded size; a
        # contemporaneous window is also the editorially right candidate set
        # (later engagement belongs to the cited-by panel, not this widget).
        date_cutoff = opinion.release_date - timedelta(days=3 * 365)
        date_ceiling = opinion.release_date + timedelta(days=3 * 365)

    # Candidates come from the SLIM table (see search_similar_opinions for
    # the measured rationale); the SOURCE vector still comes from the fat
    # table by primary key (one row), with its embedding_pending guard so a
    # placeholder zero-vector never becomes the query point.
    #
    # Court ids are resolved in PYTHON and inlined as literals -- NEVER via
    # an IN-subquery/join on opinions_court. EXPLAIN (2026-08-07): with the
    # join, the optimizer can only ref-access the slim PK on court_id
    # (key_len 8) and walks EVERY row of the court across all years,
    # post-filtering dates -- MN blew the 12s bound on every cold page. With
    # literals it range-scans (court_id, release_date) (key_len 11): the
    # same window is ~0.8s. Same rule as the "pre-resolve court IDs" gotcha
    # in CLAUDE.md, and the same shape search_similar_opinions already uses.
    from opinions.models import Court  # local: this module keeps model imports lazy

    court_ids = list(
        Court.objects.filter(state_id=opinion.court.state_id)
        .values_list("id", flat=True)
    )
    if not court_ids:
        return []
    placeholders = ",".join(["%s"] * len(court_ids))
    sql = [
        "SELECT e.opinion_id,",
        "       VEC_DISTANCE_COSINE(e.embedding, src.embedding) AS dist",
        "FROM opinions_opinionembedding e",
        "JOIN opinions_opinion src",
        "  ON src.id = %s AND src.embedding_pending = 0",
        "WHERE e.court_id IN (" + placeholders + ")",
        "  AND e.opinion_id != %s",
    ]
    params: list = [opinion.id, *court_ids, opinion.id]
    if date_cutoff is not None:
        sql.append("  AND e.release_date BETWEEN %s AND %s")
        params.extend([date_cutoff, date_ceiling])
    sql.append("ORDER BY dist")
    sql.append("LIMIT %s")
    params.append(limit)

    rows = _run_vector_query("\n".join(sql), params)
    if with_scores:
        return [(row[0], row[1]) for row in rows]
    return [row[0] for row in rows]
