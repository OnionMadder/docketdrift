"""Add OpinionCitation.source so bulk and extracted edges can coexist.

Two things this migration is deliberately careful about, both learned the hard
way on this database:

1. **`max_statement_time` must be lifted first.** settings.py puts a 25s cap on
   every connection, which is right for web requests and fatal for schema
   changes -- migration 0023's ALTER ran ~30s and was killed mid-statement,
   leaving the schema half-applied.

2. **No index on the new column, so ADD COLUMN stays cheap.** InnoDB cannot do
   an INSTANT add for an indexed column; it falls back to a rebuild, and
   rebuilding this table is the same class of operation that burned 9 hours on
   the VECTOR INDEX attempt. Nothing filters on `source` alone -- queries
   always narrow by citing/cited opinion first -- so an index buys nothing.

Backfill: existing rows are marked by what only the extractor produces. The
bulk loader writes no context (CourtListener's citation map carries none), so a
non-empty `context` is an exact marker for an extracted edge.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('opinions', '0027_opinioncitation_clustering'),
    ]

    operations = [
        migrations.RunSQL(
            "SET SESSION max_statement_time = 0",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddField(
            model_name='opinioncitation',
            name='source',
            field=models.CharField(choices=[('bulk', 'CourtListener bulk citation map'), ('extracted', "Extracted from this opinion's text")], default='extracted', help_text="Where this edge came from. 'bulk' = CourtListener's citation-map export (load_citation_edges): resolved against THEIR full corpus, so it can reach cases we don't hold, but carries no context quote and no treatment. 'extracted' = parsed from the citing opinion's own text (extract_citations): carries a quote and a classified treatment, and is the only source that can cover opinions CourtListener has no data for. Both are kept -- the two sources resolve different things, so neither supersedes the other. Display prefers 'extracted' when both describe the same pair.", max_length=16),
        ),
        # Everything already in the table with no context came from the bulk
        # loader. Raw SQL on purpose: a RunPython row-by-row pass over 605K
        # rows would be far slower and risks the dropped-connection (2013)
        # failure documented for long-held cursors on this host.
        migrations.RunSQL(
            "UPDATE opinions_opinioncitation SET source = 'bulk' "
            "WHERE context = '' OR context IS NULL",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
