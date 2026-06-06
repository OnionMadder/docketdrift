"""Add a MariaDB FULLTEXT index on Opinion.raw_text and title.

Without FULLTEXT, every search and every explore-tags count is a full-table
LIKE '%phrase%' scan over the entire raw_text corpus (~1.8 GB of body text
on the 60K-row MN corpus, ~5-10s per query on shared hosting). The
context processor runs 20 such queries on a cold cache, easily blowing
gunicorn's 60s timeout and triggering the 500-error storm we saw today.

With FULLTEXT + MATCH AGAINST, the same searches run in milliseconds.

This migration is RAW SQL because Django's Index() doesn't expose the
FULLTEXT index type. It runs ONLY on MySQL/MariaDB (skipped silently on
SQLite for local dev), so the migration is safe everywhere.

Caveats:
- MariaDB's default ``ft_min_word_len`` is 4 chars (InnoDB: 3). Shorter
  query terms won't match via FULLTEXT -- the view falls back to LIKE
  for short queries. Default stopwords ("the", "is", etc.) are filtered.
- Adding the FULLTEXT index on the existing 60K-row corpus may take 5-15
  minutes the first time because MariaDB has to scan + tokenize every
  raw_text body. Subsequent INSERTs maintain the index incrementally
  (no rebuild cost).
"""
from django.db import migrations


def add_fulltext(apps, schema_editor):
    """Add FULLTEXT(raw_text, title) on opinions_opinion."""
    if schema_editor.connection.vendor != "mysql":
        return  # SQLite / Postgres: skip silently
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE opinions_opinion "
            "ADD FULLTEXT INDEX opinion_text_fulltext (raw_text, title)"
        )


def remove_fulltext(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE opinions_opinion DROP INDEX opinion_text_fulltext"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0011_seed_nh"),
    ]

    operations = [
        migrations.RunPython(add_fulltext, remove_fulltext),
    ]
