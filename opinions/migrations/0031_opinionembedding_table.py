"""Create the slim embedding table that makes cosine scans feasible again.

WHY THIS TABLE EXISTS. The cosine scans read ``Opinion.embedding`` out of the
2.75GB fat table, dragging every clustered row (with 50-100KB of raw_text
alongside) through NFSN's 8MB buffer pool. Measured 2026-08-05: ~2,400 rows/s,
so MN (69K embedded rows) needs ~29s against a 12s bound -- every MN/AZ scan
was killed, every time. This table packs (court_id, release_date, opinion_id,
embedding) densely: a full MN scan is ~290MB sequential instead of gigabytes
of scattered pages, and the clustered PRIMARY KEY (court_id, release_date,
opinion_id) makes a date-windowed scan read ONLY the window's pages.

WHY NO VECTOR INDEX. The 2026-06-26/27 attempts proved HNSW infeasible here
(16MB global mhnsw cache, 8MB buffer pool, no SUPER; the "slim table" variant
of that attempt failed at the INDEX BUILD step, degrading to ~11 rows/s).
This table deliberately has NO secondary indexes at all -- it exists to make
the O(N) scan cheap, not to replace it. Do not add a VECTOR INDEX to it
without re-reading the CLAUDE.md gotcha.

WHY RAW SQL, NO DJANGO MODEL. Same precedent as ``Opinion.embedding`` itself:
Django's ORM doesn't speak VECTOR, and every consumer (semantic.py,
embed_opinions, sync_embedding_table) uses raw cursors anyway. An unmanaged
model would only invite accidental ORM usage.

Populated by ``sync_embedding_table`` (backfill + repair) and kept current by
``embed_opinions`` (dual-write). Rows carry only EMBEDDED opinions -- the
placeholder zero-vectors of embedding_pending rows never enter this table, so
the scans need no embedding_pending predicate.

CREATE TABLE of an empty table is instant; no max_statement_time concern.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0030_drop_queryembedding"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE TABLE IF NOT EXISTS opinions_opinionembedding ("
                "  court_id BIGINT NOT NULL,"
                "  release_date DATE NOT NULL,"
                "  opinion_id BIGINT NOT NULL,"
                "  embedding VECTOR(1024) NOT NULL,"
                "  PRIMARY KEY (court_id, release_date, opinion_id)"
                ") ENGINE=InnoDB"
            ),
            reverse_sql="DROP TABLE IF EXISTS opinions_opinionembedding",
        ),
    ]
