"""Add Opinion.embedding -- MariaDB 11.7 native VECTOR(1024) column.

Holds the voyage-law-2 embedding (1024 floats) for each opinion. Nullable
because we populate it via an overnight batch run (``manage.py
embed_opinions``) rather than at row-creation time -- new opinions get
their embedding via the save-hook later.

Storage cost: ~2 KB per row (16-bit packed floats), so 60K rows = ~120 MB.
Tiny.

The vector INDEX (HNSW for fast nearest-neighbor search) is added in a
SEPARATE migration AFTER the corpus is fully embedded -- building an
HNSW index over NULL/sparse data is wasted work; better to add it once
we have a populated column. See migration 0014_opinion_embedding_index
when it lands.

MariaDB-only: skipped silently on SQLite for local dev. The
``embed_opinions`` command early-exits on non-MariaDB backends.
"""
from django.db import migrations


def add_vector(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE opinions_opinion "
            "ADD COLUMN embedding VECTOR(1024) NULL"
        )


def drop_vector(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("ALTER TABLE opinions_opinion DROP COLUMN embedding")


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0012_opinion_fulltext_index"),
    ]

    operations = [
        migrations.RunPython(add_vector, drop_vector),
    ]
