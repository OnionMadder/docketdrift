"""Seed initial State rows.

The live subdomain (mn.docketdrift.com) needs ``State(code='MN')`` in the DB
for ``opinions.middleware.StateRouterMiddleware`` to resolve. This migration
makes that automatic on a fresh deploy instead of requiring a manual shell
seed. ``update_or_create`` keeps it idempotent across re-runs.

To add a state, append it to ``INITIAL_STATES`` and create a NEW data
migration -- don't edit this one once it's been applied in production.
"""
from django.db import migrations


INITIAL_STATES = [
    {"code": "MN", "name": "Minnesota", "slug": "mn", "is_live": True},
]


def seed_states(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    for entry in INITIAL_STATES:
        State.objects.update_or_create(
            code=entry["code"],
            defaults={k: v for k, v in entry.items() if k != "code"},
        )


def unseed_states(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    State.objects.filter(code__in=[s["code"] for s in INITIAL_STATES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_states, unseed_states),
    ]
