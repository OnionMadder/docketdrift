"""Court-rule citation layer.

New TABLE, deliberately not new columns on opinions_opinion: that table
is 2.75GB and an indexed ADD COLUMN there was the 9-hour unkillable
COPY rebuild from the VECTOR INDEX attempt. RuleCitation starts empty,
so CreateModel is metadata plus one FK constraint.

The ``max_statement_time = 0`` opener is the standard migration
preamble in this repo: settings.py puts a 25s cap on EVERY connection,
which is right for web requests and has killed schema operations
mid-statement (errno 1317) leaving a half-applied schema. The SET only
affects this migration's connection.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0043_judge_photo_credit"),
    ]

    operations = [
        migrations.RunSQL(
            "SET SESSION max_statement_time = 0",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.CreateModel(
            name="RuleCitation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference_slug", models.CharField(db_index=True, help_text="Normalized lowercase rule reference, URL-safe. e.g. 'minn.r.civ.app.p.136.01.subd.1' or 'minn.r.admin.3310.2921'.", max_length=64)),
                ("reference_display", models.CharField(help_text="Canonical display form, e.g. 'Minn. R. Civ. App. P. 136.01, subd. 1(c)'.", max_length=128)),
                ("rule_set", models.CharField(db_index=True, help_text="Which body of rules: civ.app.p, civ.p, crim.p, evid, ... or 'admin'.", max_length=24)),
                ("rule_number", models.CharField(db_index=True, help_text="Rule number portion (136.01, 60.02, 404, 3310.2921).", max_length=16)),
                ("subdivision", models.CharField(blank=True, default="", help_text="subd. N for court rules, subp. N for administrative. Blank when absent.", max_length=16)),
                ("subsection", models.CharField(blank=True, default="", help_text="Parenthetical subsection, e.g. the 'c' of 136.01, subd. 1(c). Display-only.", max_length=16)),
                ("is_boilerplate", models.BooleanField(default=False, help_text="True when this occurrence is the nonprecedential-opinion disclaimer rather than a substantive citation. Kept, not dropped.")),
                ("text_offset", models.IntegerField(default=0, help_text="Character offset in opinion.raw_text where this citation starts.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("opinion", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rule_citations", to="opinions.opinion")),
            ],
            options={
                "ordering": ["opinion", "text_offset"],
                "indexes": [
                    models.Index(fields=["reference_slug"], name="opinions_ru_referen_9ada39_idx"),
                    models.Index(fields=["rule_set", "rule_number"], name="opinions_ru_rule_se_1198d6_idx"),
                    models.Index(fields=["reference_slug", "is_boilerplate"], name="rule_slug_boiler_idx"),
                ],
            },
        ),
    ]
