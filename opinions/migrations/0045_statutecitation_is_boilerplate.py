"""Flag procedural-notice statute citations without rebuilding the table.

ADD COLUMN ONLY -- NO INDEX, and that is the whole design of this
migration. ``opinions_statutecitation`` already holds 636,360 rows, and
this repo's scar tissue on big ALTERs is specific: ``ALGORITHM=INSTANT``
is a pure metadata change that returns in milliseconds, but INSTANT
cannot cover an indexed column, and ``NOCOPY`` is not a fast-path
guarantee (migration 0026 took 39 minutes under it).

Skipping the index costs nothing measurable, because nothing filters on
this column at scale:

  * ``statute_detail`` and the MCP ``get_statute`` tool already fold
    everything the page needs into ONE range scan over the existing
    (reference_slug) index, then split boilerplate from substantive in
    Python over a few thousand tuples.
  * The exact-COUNT fallback only fires past ROW_CAP = 50,000 rows for a
    single slug. The heaviest statute in the corpus is 7,436.
  * ``corpus_insights`` is a batch command that lifts
    max_statement_time, so a sequential scan there is fine.

RuleCitation carries a (reference_slug, is_boilerplate) composite, but
it got one free at CreateModel time on an empty table. Adding the same
index here would mean a real build over 636K rows to serve a query that
never runs. If a future caller does need it, build it as a separate
online CREATE INDEX with ALGORITHM=INPLACE, LOCK=NONE -- killable,
unlike the ADD VECTOR INDEX disaster -- rather than folding it in here.

The ``max_statement_time = 0`` opener is the standard preamble: settings
puts a 25s cap on every connection, which has killed schema operations
mid-statement (errno 1317) and left a half-applied schema.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0044_rulecitation"),
    ]

    operations = [
        migrations.RunSQL(
            "SET SESSION max_statement_time = 0",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddField(
            model_name="statutecitation",
            name="is_boilerplate",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True when this occurrence is a procedural notice "
                    "rather than the court relying on the statute. "
                    "Kept, not dropped."
                ),
            ),
        ),
    ]
