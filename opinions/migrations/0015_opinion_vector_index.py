"""Add HNSW vector index on Opinion.embedding for fast similarity search.

Without the index, ``VEC_DISTANCE_COSINE`` across 60K opinions does a
full table scan -- workable for one-off queries but unacceptable for
the per-request Scan endpoint. With the HNSW index, k-nearest-neighbor
queries land in single-digit milliseconds regardless of corpus size.

MariaDB-only: skipped silently on SQLite for local dev. The semantic
search helper module short-circuits when the embedding column isn't
present (vendor != mysql) so local dev still works.
"""
from django.db import migrations


def add_index(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    with schema_editor.connection.cursor() as cursor:
        # MariaDB 11.7+ VECTOR INDEX defaults to HNSW with cosine distance.
        # If the column already has populated vectors, the index builds in
        # the background; this ALTER TABLE returns quickly.
        cursor.execute(
            "ALTER TABLE opinions_opinion "
            "ADD VECTOR INDEX opinion_embedding_idx (embedding)"
        )


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE opinions_opinion DROP INDEX opinion_embedding_idx"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0014_add_state_request"),
    ]

    operations = [
        migrations.RunPython(add_index, drop_index),
    ]
