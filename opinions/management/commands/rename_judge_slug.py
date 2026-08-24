"""Rename a Judge slug, leaving a permanent redirect behind.

A judge slug is a public URL key. Changing one without a forwarding
address breaks indexed pages, inbound links, and any AI answer that
grounded a citation on that URL. This command makes the safe path the
easy path: it writes the old slug into JudgeSlugAlias inside the same
transaction that changes the live slug, so a 301 exists from the instant
the old URL stops working.

Two modes:

  --slug OLD --to NEW          rename one judge
  --state NH --strip-prefix fallback-
                               rename every judge in a state whose slug
                               starts with the prefix, dropping it

Refuses to write a slug that would collide with another judge in the
same state (Judge.Meta.unique_together = (state, slug)), and refuses to
create an alias that shadows a LIVE slug -- an alias must never win over
a real dossier.

Dry-run by default; --apply commits.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from opinions.models import Judge, JudgeSlugAlias


class Command(BaseCommand):
    help = "Rename judge slug(s), recording a 301 alias for the old value."

    def add_arguments(self, parser):
        parser.add_argument("--state", default=None, help="State code, e.g. NH.")
        parser.add_argument("--slug", default=None, help="Existing slug to rename.")
        parser.add_argument("--to", default=None, help="New slug.")
        parser.add_argument("--strip-prefix", default=None,
                            help="Bulk mode: drop this prefix from every matching slug.")
        parser.add_argument("--note", default="",
                            help="Why (stored on the alias, admin-only).")
        parser.add_argument("--apply", action="store_true",
                            help="Commit. Omit for a dry-run preview.")

    def handle(self, *args, state, slug, to, strip_prefix, note, apply, **options):
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        if strip_prefix:
            if not state:
                raise CommandError("--strip-prefix requires --state.")
            targets = list(Judge.objects.filter(
                state__code=state.upper(), slug__startswith=strip_prefix))
            pairs = [(j, j.slug[len(strip_prefix):]) for j in targets]
        elif slug and to:
            qs = Judge.objects.filter(slug=slug)
            if state:
                qs = qs.filter(state__code=state.upper())
            j = qs.first()
            if not j:
                raise CommandError(f"No judge with slug {slug!r}.")
            pairs = [(j, to)]
        else:
            raise CommandError("Give either --slug/--to or --state/--strip-prefix.")

        if not pairs:
            self.stdout.write("Nothing matches; nothing to do.")
            return

        mode = "APPLY" if apply else "DRY-RUN (nothing written; pass --apply)"
        self.stdout.write(self.style.SUCCESS(f"rename_judge_slug — {mode}\n"))

        renamed = skipped = 0
        for judge, new_slug in pairs:
            new_slug = (new_slug or "").strip("-")
            if not new_slug:
                self.stdout.write(self.style.WARNING(
                    f"  {judge.full_name:<28} empty target slug -- skipped"))
                skipped += 1
                continue
            if new_slug == judge.slug:
                self.stdout.write(f"  {judge.full_name:<28} already {new_slug!r}")
                continue

            # Collision: another judge in this state already owns it.
            clash = (Judge.objects.filter(state=judge.state, slug=new_slug)
                     .exclude(pk=judge.pk).first())
            if clash:
                self.stdout.write(self.style.WARNING(
                    f"  {judge.full_name:<28} target {new_slug!r} taken by "
                    f"{clash.full_name} -- skipped"))
                skipped += 1
                continue

            # An alias must never shadow a live dossier.
            live = Judge.objects.filter(state=judge.state, slug=judge.slug).exclude(pk=judge.pk).first()
            if live:
                self.stdout.write(self.style.WARNING(
                    f"  {judge.full_name:<28} old slug {judge.slug!r} is live for "
                    f"{live.full_name} -- skipped"))
                skipped += 1
                continue

            self.stdout.write(
                f"  {judge.full_name:<28} {judge.slug}  ->  {new_slug}"
                f"   (301 from old)")
            if apply:
                old = judge.slug
                with transaction.atomic():
                    judge.slug = new_slug
                    judge.save(update_fields=["slug"])
                    JudgeSlugAlias.objects.get_or_create(
                        judge=judge, slug=old,
                        defaults={"note": note or "slug corrected"},
                    )
            renamed += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Renamed' if apply else 'Would rename'}: {renamed}   skipped: {skipped}"))
        if not apply:
            self.stdout.write("Re-run with --apply to commit.")
