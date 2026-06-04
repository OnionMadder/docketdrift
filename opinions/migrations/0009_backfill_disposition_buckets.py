"""Backfill Opinion.disposition_bucket for existing rows.

Migration 0008 added the column; this fills it in for every Opinion the
DB already has. Idempotent -- recomputing on an already-correct row is a
no-op, so this migration is safe to re-run if the DB is rebuilt from
scratch.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    from opinions.utils import compute_disposition_bucket

    Opinion = apps.get_model("opinions", "Opinion")
    to_update = []
    for op in Opinion.objects.all().only("id", "disposition", "disposition_bucket"):
        bucket = compute_disposition_bucket(op.disposition)
        if bucket != op.disposition_bucket:
            op.disposition_bucket = bucket
            to_update.append(op)
    if to_update:
        # bulk_update skips Opinion.save() (and therefore the parser
        # save-hook) which is intentional -- we're only syncing the
        # derived field, no other content needs to change.
        Opinion.objects.bulk_update(to_update, ["disposition_bucket"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0008_opinion_disposition_bucket"),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
