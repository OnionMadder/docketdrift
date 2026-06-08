"""Seed Arizona state + appellate courts.

AZ is DocketDrift's third state. Two-tier appellate structure:

- ``ariz`` -- Arizona Supreme Court (CourtListener jurisdiction='S').
- ``arizctapp`` -- Arizona Court of Appeals (single CL court id covering
  both Division 1 and Division 2 -- CL aggregates them under one slug;
  if we ever need the divisions split out, that's a downstream Court
  attribute, not a separate Court row).

The State row is created with ``is_live=False`` so the apex picker
doesn't advertise an empty corpus. The Court rows are created
immediately so DNS routing + StateRouterMiddleware on
``az.docketdrift.com`` can resolve without errors, and so subsequent
``ingest_court ariz`` and ``ingest_court arizctapp`` runs have a
target Court FK to attach opinions to.

To flip live: ``State.objects.filter(code="AZ").update(is_live=True)``
once we have a respectable corpus (typically: first ingest_court pass
completed, embed_opinions run for semantic search, statute extractor
generalized -- or shipped without it for v1, AZ pages still work
without /statute/ links).
"""
from django.db import migrations


def seed(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    Court = apps.get_model("opinions", "Court")

    state, _ = State.objects.update_or_create(
        code="AZ",
        defaults={
            "name": "Arizona",
            "slug": "az",
            "is_live": False,
        },
    )
    Court.objects.update_or_create(
        state=state,
        level="SUPREME",
        defaults={
            "name": "Arizona Supreme Court",
            "slug": "supreme",
            "courtlistener_id": "ariz",
        },
    )
    Court.objects.update_or_create(
        state=state,
        level="APPEALS",
        defaults={
            "name": "Arizona Court of Appeals",
            "slug": "appeals",
            "courtlistener_id": "arizctapp",
        },
    )


def unseed(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    Court = apps.get_model("opinions", "Court")
    Court.objects.filter(courtlistener_id__in=["ariz", "arizctapp"]).delete()
    State.objects.filter(code="AZ").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0021_tag_embedded_at_tag_embedding_tagsuggestion"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
