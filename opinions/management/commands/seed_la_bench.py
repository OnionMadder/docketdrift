"""Seat the sitting Louisiana benches from ``opinions/data/la_bench_2026.json``.

The data file encodes an EXPLICIT per-judge decision made against the
2026-08-25 post-purge probe (see docs/TODO.md): ``complete`` renames a
verified surname-only byline-learned row to the roster full name (slug
untouched -- the NH orthography rule), ``exact`` only sets role/status
on a row already carrying the full name, ``create`` adds a new row.
Mixed-span rows that hold two real people's votes (Lanier Jr/III,
Enos vs Page McClendon, ...) are deliberately not completed.

Guards, per the AZ/NH seating discipline:

- A ``complete``/``exact`` pk must exist, sit on the expected court,
  and (for complete) be a single-token surname matching the roster
  name's surname; anything else is REFUSED and reported.
- A ``create`` is refused if a row with that exact full_name already
  exists in the court (idempotency) -- it then behaves like ``exact``.
- Roles come from the data file; a chief title is only ever printed
  when the court's own site states it.
- Dry-run by default; ``--apply`` to commit.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils.text import slugify

from opinions.models import Judge

DATA = Path(__file__).resolve().parents[2] / "data" / "la_bench_2026.json"


class Command(BaseCommand):
    help = "Seat the sitting LA benches from data/la_bench_2026.json."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually write (default: dry-run report).")

    def handle(self, *args, apply, **options):
        from opinions.judge_merge import surname as jsn

        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        spec = json.loads(DATA.read_text(encoding="utf-8"))
        completed = created = exact = refused = 0

        for court_id_s, court in spec["courts"].items():
            court_id = int(court_id_s)
            for entry in court["judges"]:
                name = entry["name"]
                action = entry["action"]
                role = (court["role_chief"] if entry.get("chief")
                        else court["role_member"])
                bio_url = entry.get("bio_url", "")

                if action in ("complete", "exact"):
                    judge = Judge.objects.filter(pk=entry["pk"]).first()
                    if judge is None or judge.court_id != court_id:
                        self.stdout.write(self.style.WARNING(
                            f"  [REFUSE] {name}: pk {entry['pk']} missing "
                            f"or on wrong court "
                            f"({judge.court_id if judge else None})."
                        ))
                        refused += 1
                        continue
                    if action == "complete" and judge.full_name != name:
                        # A prior apply already renamed the row; accept
                        # its own finished state (idempotent re-runs).
                        if (" " in judge.full_name.strip()
                                or jsn(name).lower()
                                != judge.full_name.strip().lower()):
                            self.stdout.write(self.style.WARNING(
                                f"  [REFUSE] {name}: pk {entry['pk']} is "
                                f"{judge.full_name!r}, not the expected "
                                "surname-only row."
                            ))
                            refused += 1
                            continue
                    elif jsn(judge.full_name).lower() != jsn(name).lower():
                        self.stdout.write(self.style.WARNING(
                            f"  [REFUSE] {name}: pk {entry['pk']} is "
                            f"{judge.full_name!r} -- surname mismatch."
                        ))
                        refused += 1
                        continue
                    tag = "complete" if action == "complete" else "exact"
                    self.stdout.write(
                        f"  [{tag:8s}] pk={judge.pk} {judge.full_name!r} "
                        f"-> {name!r}  role={role} status=ACTIVE"
                    )
                    if apply:
                        judge.full_name = name
                        judge.role = role
                        judge.status = Judge.Status.ACTIVE
                        judge.is_currently_seated = True
                        if bio_url and not judge.bio_url:
                            judge.bio_url = bio_url
                        judge.save(update_fields=[
                            "full_name", "role", "status",
                            "is_currently_seated", "bio_url"])
                    if action == "complete":
                        completed += 1
                    else:
                        exact += 1
                    continue

                # action == create
                existing = Judge.objects.filter(
                    court_id=court_id, full_name=name).first()
                if existing:
                    self.stdout.write(
                        f"  [exists  ] {name!r} already pk={existing.pk}; "
                        f"setting role/status only."
                    )
                    if apply:
                        existing.role = role
                        existing.status = Judge.Status.ACTIVE
                        existing.is_currently_seated = True
                        if bio_url and not existing.bio_url:
                            existing.bio_url = bio_url
                        existing.save(update_fields=[
                            "role", "status", "is_currently_seated",
                            "bio_url"])
                    exact += 1
                    continue
                slug = slugify(name)
                if Judge.objects.filter(slug=slug).exists():
                    slug = f"{slug}-{court_id}"
                    if Judge.objects.filter(slug=slug).exists():
                        self.stdout.write(self.style.WARNING(
                            f"  [REFUSE] {name}: slug collision on "
                            f"{slug!r}."
                        ))
                        refused += 1
                        continue
                self.stdout.write(
                    f"  [create  ] {name!r}  court={court_id} role={role} "
                    f"slug={slug}"
                )
                if apply:
                    Judge.objects.create(
                        state_id="LA",
                        court_id=court_id,
                        full_name=name,
                        slug=slug,
                        status=Judge.Status.ACTIVE,
                        is_currently_seated=True,
                        role=role,
                        bio_url=bio_url,
                    )
                created += 1

        mode = "APPLIED" if apply else "DRY RUN"
        self.stdout.write(self.style.SUCCESS(
            f"\n{mode}: completed={completed} exact={exact} "
            f"created={created} refused={refused}"
        ))
