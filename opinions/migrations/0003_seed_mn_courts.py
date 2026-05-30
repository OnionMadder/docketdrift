"""Seed Minnesota's appellate courts.

After 0002 created the MN State row, this seeds the two appellate courts
DocketDrift covers: the Minnesota Supreme Court and the Minnesota Court of
Appeals. CourtListener identifiers verified against the live
/api/rest/v4/courts/ endpoint on 2026-05-30 (both in_use=true with active
opinion scrapers; jurisdiction 'S' and 'SA' respectively).

To add a court, append to ``INITIAL_COURTS`` and create a NEW data migration --
don't edit this one once it's been applied in production.
"""
from django.db import migrations


INITIAL_COURTS = [
    {
        "state_code": "MN",
        "level": "SUPREME",
        "name": "Minnesota Supreme Court",
        "slug": "supreme",
        "courtlistener_id": "minn",
    },
    {
        "state_code": "MN",
        "level": "APPEALS",
        "name": "Minnesota Court of Appeals",
        "slug": "appeals",
        "courtlistener_id": "minnctapp",
    },
]


def seed_courts(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    Court = apps.get_model("opinions", "Court")
    for entry in INITIAL_COURTS:
        state = State.objects.get(code=entry["state_code"])
        Court.objects.update_or_create(
            state=state,
            level=entry["level"],
            defaults={
                "name": entry["name"],
                "slug": entry["slug"],
                "courtlistener_id": entry["courtlistener_id"],
            },
        )


def unseed_courts(apps, schema_editor):
    Court = apps.get_model("opinions", "Court")
    Court.objects.filter(
        courtlistener_id__in=[c["courtlistener_id"] for c in INITIAL_COURTS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0002_seed_states"),
    ]

    operations = [
        migrations.RunPython(seed_courts, unseed_courts),
    ]
