# Generated for the "How this document has been cited" feature (Phase 16b).
#
# Adds the clustering substrate to OpinionCitation: a clean display quote,
# its embedding, and the (cluster_label, is_cluster_lead) pair that groups
# near-identical citing passages into Google-Scholar-style "and N similar
# citations" rows. The two indexed columns build an index over the
# OpinionCitation table, so lift the per-statement timeout (settings'
# init_command pins it to 25s) the same way migration 0023/0026 do.
from django.db import migrations, models


def prepare_connection(apps, schema_editor):
    """Lift the statement cap and forbid a COPY-algorithm rebuild.

    ``max_statement_time = 0`` because settings' init_command pins every
    connection to 25s, too tight for the two index builds below.

    ``alter_algorithm = 'NOCOPY'`` for the same reason migration 0026 sets it:
    OpinionCitation carries ~700K edges, and a COPY-algorithm ALTER rebuilds
    the whole table -- the failure mode that made the 2026-06-26 VECTOR INDEX
    attempt unkillable for 9+ hours. NOCOPY makes MariaDB raise errno 1845
    immediately rather than start a rebuild we cannot abort. If it does
    raise, split the offending column out; do not remove the guard.

    Vendor-guarded so local SQLite dev is a clean no-op.
    """
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION max_statement_time = 0")
        cursor.execute("SET SESSION alter_algorithm = 'NOCOPY'")


class Migration(migrations.Migration):

    dependencies = [
        ('opinions', '0026_opinion_holding_extracted_at_opinion_holding_model_and_more'),
    ]

    operations = [
        migrations.RunPython(
            prepare_connection,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name='opinioncitation',
            name='context_quote',
            field=models.TextField(blank=True, default='', help_text='Clean, sentence-trimmed passage from the CITING opinion that states the proposition this case is cited for -- the public "How this document has been cited" quote (Google-Scholar style). Set by the extractor; empty when no clean sentence was found.'),
        ),
        migrations.AddField(
            model_name='opinioncitation',
            name='context_embedding',
            field=models.JSONField(blank=True, null=True, help_text='voyage-law-2 embedding of context_quote (1024 floats), set by embed_citations. Null until embedded. Used only to cluster near-identical citing passages (cluster_citations); not indexed and never read at request time.'),
        ),
        migrations.AddField(
            model_name='opinioncitation',
            name='cluster_label',
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True, help_text="Index of the similar-passage cluster this edge belongs to, WITHIN its cited_opinion's incoming citations. Set by cluster_citations; null until clustered or for external edges."),
        ),
        migrations.AddField(
            model_name='opinioncitation',
            name='is_cluster_lead',
            field=models.BooleanField(db_index=True, default=False, help_text='True for the one representative edge per cluster -- the quote shown in "How this document has been cited"; the rest are counted as "and N similar citations".'),
        ),
    ]
