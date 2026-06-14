"""Add Opinion.embedding_pending so embed batch fetch can use an index.

The embed_opinions command's hot-loop SELECT is::

    SELECT id, raw_text FROM opinions_opinion
    WHERE embedding IS NULL AND raw_text != '' AND court_id IN (...)
    ORDER BY id LIMIT 128

The `embedding IS NULL` predicate can't use an index because
`embedding` is a raw-SQL VECTOR column that Django doesn't model and
that MariaDB can't index for NULL-ness on the column itself. With AZ
at 3.5% embedded that meant every batch fetch did a near-full scan
of 38K rows -- so slow that NFSN's wallclock supervisor culled each
wrapper instance after ~10 minutes, before it had embedded more than
a handful of opinions. 91 wrapper resurrections per night and only
~100 opinions/hour throughput vs. Voyage's ~3,000/hour ceiling.

Fix: add a `BooleanField` shadow that tracks the same state but is
indexable. The composite index on `(embedding_pending, court_id)`
lets the batch fetch be sub-100ms regardless of corpus size.

This migration:
1. Adds the column with default=True (Django uses ALGORITHM=INSTANT on
   MariaDB 10.4+, so no table rewrite even on 240K rows).
2. Adds the composite index.
3. Backfills: any row where the raw-SQL `embedding` column is already
   non-NULL gets `embedding_pending=False`. This handles the existing
   MN+NH 100%-embedded rows plus the partial AZ progress.

embed_opinions is updated separately to read `embedding_pending` in
its SELECT and set `embedding_pending=False` in its UPDATE.
"""
from django.db import migrations, models


def backfill_pending_from_embedding(apps, schema_editor):
    """Mark every row whose raw VECTOR `embedding` is non-NULL as done.

    Single bulk UPDATE rather than per-row -- on 240K rows with ~82K
    already-embedded it runs in seconds. Touches the embedding column
    read-only via IS NOT NULL, doesn't rewrite anything in it.
    """
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        # Local SQLite dev has neither the VECTOR column nor a
        # meaningful embed_opinions run, so default True is correct.
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE opinions_opinion "
            "SET embedding_pending = FALSE "
            "WHERE embedding IS NOT NULL"
        )


def reverse_backfill(apps, schema_editor):
    # No-op: the RemoveField in reverse_migration drops the column
    # entirely. Backwards from there is a column re-add with default,
    # which the schema operation handles on its own.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0022_seed_az"),
    ]

    operations = [
        # Disable the per-statement timeout for THIS connection before
        # the schema ops. The DATABASES init_command sets
        # max_statement_time = 25, which is right for web requests but
        # too tight for ALTER TABLE / CREATE INDEX on a 240K-row
        # opinions_opinion -- both ran ~30s on the first attempt and got
        # killed with errno 1317 ("Query execution was interrupted"),
        # leaving the migration half-applied. The reverse no-ops because
        # the reset only lives for this connection.
        migrations.RunSQL(
            "SET SESSION max_statement_time = 0",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddField(
            model_name="opinion",
            name="embedding_pending",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Set to False after embed_opinions writes a vector to the raw "
                    "SQL `embedding` VECTOR column. Indexed alongside court_id so "
                    "the embed batch fetch (`WHERE embedding_pending = TRUE AND "
                    "court_id IN (...)`) is a fast index scan instead of a full "
                    "table walk against the unindexable VECTOR column's IS NULL "
                    "predicate. Without this column, every embed batch on a "
                    "low-coverage state scanned the whole opinions_opinion table "
                    "and put the wrapper's lifetime at ~10 min before NFSN's "
                    "wallclock supervisor culled it for sustained CPU."
                ),
            ),
        ),
        migrations.AddIndex(
            model_name="opinion",
            index=models.Index(
                fields=["embedding_pending", "court_id"],
                name="op_pending_court_idx",
            ),
        ),
        migrations.RunPython(
            backfill_pending_from_embedding,
            reverse_code=reverse_backfill,
        ),
    ]
