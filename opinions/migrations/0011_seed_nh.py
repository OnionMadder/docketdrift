"""Seed New Hampshire state + Supreme Court.

NH is the second state DocketDrift scaffolds. We're prepping the
multi-tenant architecture against a state with a SINGLE-tier appellate
structure -- NH has no intermediate Court of Appeals; appeals go straight
from trial courts to the NH Supreme Court. Useful stress test of the
``Court.Meta.unique_together = (state, level)`` constraint (no Appeals row
for NH is the right shape, not a missing row).

The State row is created with ``is_live=False`` so it stays off the apex
state picker until MN reaches v1 quality and we manually flip the flag.
The Court row exists immediately so DNS routing + StateRouterMiddleware on
nh.docketdrift.com can resolve without errors.

CourtListener identifier ('nh') verified against the local 2026-03-31
courts bulk dump 2026-06-05 (in_use=true, jurisdiction='S', active opinion
scraper). Federal ('nhd', 'nhb', 'circtdnh') and historical ('nhsuperct',
'nhprivycounnh') NH-related court ids are intentionally ignored -- only
the state appellate court is in scope.
"""
from django.db import migrations


def seed(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    Court = apps.get_model("opinions", "Court")

    state, _ = State.objects.update_or_create(
        code="NH",
        defaults={
            "name": "New Hampshire",
            "slug": "nh",
            "is_live": False,  # not advertised on the apex picker yet
        },
    )
    Court.objects.update_or_create(
        state=state,
        level="SUPREME",
        defaults={
            "name": "Supreme Court of New Hampshire",
            "slug": "supreme",
            "courtlistener_id": "nh",
        },
    )


def unseed(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    Court = apps.get_model("opinions", "Court")
    Court.objects.filter(courtlistener_id="nh").delete()
    State.objects.filter(code="NH").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0010_opinion_review_status"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
