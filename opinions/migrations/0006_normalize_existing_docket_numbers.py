"""Normalize existing Opinion.case_number values to the canonical format.

After this migration runs, all rows have ``case_number`` in dashed
uppercase form (e.g. ``A25-1191``, ``ADM24-0001``). New rows arrive
already canonical because ``opinions.management.commands.ingest_court``
runs the same normalization at ingest time.

Idempotent -- normalizing an already-canonical value is a no-op, so this
migration can be re-applied safely if the DB is rebuilt from scratch.
"""
from django.db import migrations


def normalize_existing(apps, schema_editor):
    from opinions.utils import normalize_docket_number

    Opinion = apps.get_model("opinions", "Opinion")
    to_update = []
    for op in Opinion.objects.all().only("id", "case_number"):
        canonical = normalize_docket_number(op.case_number)
        if canonical != op.case_number:
            op.case_number = canonical
            to_update.append(op)
    if to_update:
        # bulk_update skips Opinion.save() (and therefore the parser
        # save-hook) which is intentional -- we're only fixing the
        # docket-number formatting, raw_text and ParseLog are untouched.
        Opinion.objects.bulk_update(to_update, ["case_number"])


def noop_reverse(apps, schema_editor):
    # Going backward we'd have to remember the original format per row,
    # which we don't track. Reverse migration is a no-op; the data is
    # still readable and the canonical form is harmless.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0005_opinion_disposition_parselog"),
    ]

    operations = [
        migrations.RunPython(normalize_existing, noop_reverse),
    ]
