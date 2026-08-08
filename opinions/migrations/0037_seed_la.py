"""Seed Louisiana state + appellate courts (LA Phase 1).

LA is DocketDrift's fourth state and the first that uses
``Court.division`` at scale (five COA circuits from day one). Structure
that shapes this migration:

- Louisiana Supreme Court -- one row, division="". CL id ``la``.
- Louisiana Court of Appeal, First..Fifth Circuits -- five rows,
  division="1".."5". CL groups all five under one id ``lactapp``, so
  we give ONE circuit the shared id and mint synthetic ids
  ``lactapp-2``..``lactapp-5`` for the others (mirrors AZ's
  ``arizctapp`` split shape, ``courtlistener_id`` must be unique in
  our schema). ``assign_la_circuits`` (Phase 7c) re-homes bulk-ingested
  ``lactapp`` opinions to their real circuit later; until it runs,
  every COA opinion lands on the First Circuit row.

  Which circuit is the "landing" row is arbitrary -- First Circuit
  wins because it's the natural default in a set that will always be
  re-homed. Do not read anything else into it.

is_live=False -- the apex picker doesn't advertise an empty corpus.
DNS + StateRouterMiddleware on ``la.docketdrift.com`` can resolve
without errors once this migration runs.

Idempotent -- update_or_create keys on (state, level, division), which
matches Opinion.Meta.unique_together and is safe to re-apply.
"""
from django.db import migrations


LA_CIRCUITS = [
    # (division, ordinal_name, courtlistener_id)
    ("1", "First",  "lactapp"),
    ("2", "Second", "lactapp-2"),
    ("3", "Third",  "lactapp-3"),
    ("4", "Fourth", "lactapp-4"),
    ("5", "Fifth",  "lactapp-5"),
]


def seed(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    Court = apps.get_model("opinions", "Court")

    state, _ = State.objects.update_or_create(
        code="LA",
        defaults={
            "name": "Louisiana",
            "slug": "la",
            "is_live": False,
        },
    )
    Court.objects.update_or_create(
        state=state,
        level="SUPREME",
        division="",
        defaults={
            "name": "Louisiana Supreme Court",
            "slug": "supreme",
            "courtlistener_id": "la",
        },
    )
    for div, ordinal, cl_id in LA_CIRCUITS:
        Court.objects.update_or_create(
            state=state,
            level="APPEALS",
            division=div,
            defaults={
                "name": f"Louisiana Court of Appeal, {ordinal} Circuit",
                "slug": f"appeals-{div}",
                "courtlistener_id": cl_id,
            },
        )


def unseed(apps, schema_editor):
    State = apps.get_model("opinions", "State")
    Court = apps.get_model("opinions", "Court")
    cl_ids = ["la"] + [x[2] for x in LA_CIRCUITS]
    Court.objects.filter(courtlistener_id__in=cl_ids).delete()
    State.objects.filter(code="LA").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0036_opinion_unique_with_release_date"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
