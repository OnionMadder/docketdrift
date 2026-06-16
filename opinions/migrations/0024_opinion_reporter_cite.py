"""Add Opinion.reporter_cite -- the canonical citation others use to cite this
opinion (NH neutral cites for now), the resolution key for the citation graph
and paste-a-cite search routing.

SET max_statement_time = 0 first so the index build on the 240K-row
opinions_opinion table doesn't trip settings' 25s per-statement cap (see
CLAUDE.md "Long migrations trip max_statement_time = 25"). The SET only
affects this migration's connection.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0023_opinion_embedding_pending"),
    ]

    operations = [
        migrations.RunSQL(
            sql="SET SESSION max_statement_time = 0",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddField(
            model_name="opinion",
            name="reporter_cite",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text=(
                    "Canonical reporter citation OTHERS use to cite this "
                    "opinion, e.g. '2026 N.H. 7' (NH neutral cite). Populated "
                    "by the state parser. The resolution key for the "
                    "OpinionCitation graph and for paste-a-cite search routing."
                ),
                max_length=64,
            ),
        ),
    ]
