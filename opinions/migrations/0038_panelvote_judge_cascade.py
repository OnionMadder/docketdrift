"""Change PanelVote.judge on_delete from PROTECT to CASCADE.

Before: attempting to delete a Judge with any panel votes raised
ProtectedError, so editors had to manually delete every PanelVote
first -- and PanelVote has no changelist-level access from a Judge's
admin page. Result: mystery/phantom judges were effectively
undeleteable via the admin, encouraging workarounds (leaving them in,
marking them with cl_absent, etc.) that muddy the roster.

After: deleting a Judge cascades to their PanelVotes. Opinions
themselves are UNAFFECTED -- Opinion has no direct FK to Judge; the
only relationship runs through PanelVote. So a Judge deletion
destroys only the participation records that pointed at the deleted
judge, which is exactly the correct semantic for a phantom judge (the
supposed participation was wrong data).

Django admin's default delete-confirmation page enumerates every
cascaded row, so editors see "will also delete N PanelVotes" before
firing. Real deletes remain deliberate; they're just no longer
blocked. The unique_together on (opinion, judge) means we never had
two votes to worry about per judge-opinion pair, so the cascade is
cleanly bounded.

Migration is a MariaDB FK-constraint metadata change (drop + recreate);
no row rewrite, no lock on opinions_opinion. max_statement_time lifted
belt-and-suspenders per the migration 0023 pattern.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('opinions', '0037_seed_la'),
    ]

    operations = [
        migrations.RunSQL(
            "SET SESSION max_statement_time = 0",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name='panelvote',
            name='judge',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='panel_votes',
                to='opinions.judge',
            ),
        ),
    ]
