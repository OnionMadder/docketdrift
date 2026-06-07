"""Seed the controlled-vocabulary starter set of editorial tags.

This is the v1 taxonomy -- ~30 tags across four categories that cover
the lions's share of MN appellate opinions. The maintainer extends
this via Django admin as new patterns emerge during manual review.

Tags are KEYED on slug; if a tag with the same slug already exists
(e.g. from a previous run, hand-add via admin) we leave it alone.
Idempotent re-run.
"""
from django.db import migrations

STARTER_TAGS = [
    # ----- DOCTRINE -----
    ("fourth-amendment", "Fourth Amendment", "doctrine",
     "Search and seizure, warrants, exclusionary rule."),
    ("fifth-amendment", "Fifth Amendment", "doctrine",
     "Self-incrimination, double jeopardy, due process, takings."),
    ("sixth-amendment", "Sixth Amendment", "doctrine",
     "Right to counsel, speedy trial, jury, confrontation."),
    ("eighth-amendment", "Eighth Amendment", "doctrine",
     "Cruel and unusual punishment, excessive fines, bail."),
    ("fourteenth-amendment", "Fourteenth Amendment", "doctrine",
     "Due process, equal protection, incorporation."),
    ("first-amendment", "First Amendment", "doctrine",
     "Speech, press, religion, assembly."),
    ("miranda", "Miranda", "doctrine",
     "Custodial interrogation warnings and waiver."),
    ("ineffective-assistance", "Ineffective assistance of counsel", "doctrine",
     "Strickland claims, performance + prejudice prong."),
    ("qualified-immunity", "Qualified immunity", "doctrine",
     "Civil rights claims against state actors."),
    ("due-process", "Due process", "doctrine",
     "Procedural + substantive due process challenges."),

    # ----- SUBJECT MATTER -----
    ("criminal-procedure", "Criminal procedure", "subject",
     "Process-side criminal cases: motions, discovery, sentencing."),
    ("family-law", "Family law", "subject",
     "Divorce, custody, support, termination of parental rights."),
    ("termination-of-parental-rights", "Termination of parental rights", "subject",
     "TPR proceedings under Minn. Stat. 260C.301 and related."),
    ("employment-law", "Employment law", "subject",
     "At-will, discrimination, wage and hour, FLSA, MHRA."),
    ("contract-law", "Contract law", "subject",
     "Formation, breach, remedies, interpretation."),
    ("workers-compensation", "Workers' compensation", "subject",
     "WCCA appeals, compensability, vocational rehab."),
    ("administrative-law", "Administrative law", "subject",
     "Agency decisions, standard of review, MAPA."),
    ("civil-procedure", "Civil procedure", "subject",
     "Process-side civil cases: pleadings, discovery, motions."),
    ("property-law", "Property law", "subject",
     "Real property, easements, title, eminent domain."),
    ("implied-consent", "Implied consent", "subject",
     "DWI implied-consent license revocation cases."),

    # ----- PROCEDURAL -----
    ("en-banc", "En banc", "procedural",
     "Decided by the full court rather than a panel."),
    ("per-curiam", "Per curiam", "procedural",
     "Unsigned opinion of the court."),
    ("concurrence", "Concurrence", "procedural",
     "Separate opinion concurring in result or reasoning."),
    ("dissent", "Dissent", "procedural",
     "Separate opinion disagreeing with the majority."),
    ("summary-judgment", "Summary judgment", "procedural",
     "Rule 56 / Rule 12.02 motions."),
    ("abuse-of-discretion", "Abuse of discretion", "procedural",
     "Standard-of-review framing for trial court determinations."),
    ("evidentiary-hearing", "Evidentiary hearing", "procedural",
     "Hearing on factual issues before disposition."),

    # ----- POSTURE / SIGNIFICANCE -----
    ("first-impression", "First impression", "posture",
     "Issue not previously decided by this court."),
    ("statutory-interpretation", "Statutory interpretation", "posture",
     "Construction of statutory text, canons of construction."),
    ("constitutional-question", "Constitutional question", "posture",
     "Facial or as-applied constitutional challenge."),
    ("unsigned-order", "Unsigned order", "posture",
     "Order disposition rather than full opinion."),
]


def seed(apps, schema_editor):
    Tag = apps.get_model("opinions", "Tag")
    for slug, label, category, description in STARTER_TAGS:
        Tag.objects.get_or_create(
            slug=slug,
            defaults={
                "label": label,
                "category": category,
                "description": description,
            },
        )


def unseed(apps, schema_editor):
    Tag = apps.get_model("opinions", "Tag")
    Tag.objects.filter(slug__in=[t[0] for t in STARTER_TAGS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0017_add_tag_and_m2m"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
