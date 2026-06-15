"""Self-host judge portraits and apply scraped NH justice bios.

Repoints every judge's ``photo_url`` to a self-hosted ``/static/`` portrait so
the site never hotlinks an external court site (which would break if that site
goes down -- and NH's is Akamai-blocked outright). Also applies the NH Supreme
Court justice bios scraped via the Playwright tool.

Reads ``opinions/data/judge_localization.json`` (produced locally by
``scripts/fetch_judge_photos.py``; the portraits themselves are committed under
``opinions/static/opinions/judges/`` and served by WhiteNoise after
``collectstatic``). Idempotent: re-running only writes changed fields.

Run AFTER ``collectstatic`` + a gunicorn restart, so the static files are
already being served when photo_url starts pointing at them.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from opinions.models import Judge

DEFAULT_MANIFEST = Path(settings.BASE_DIR) / "opinions" / "data" / "judge_localization.json"


def _parse_date(s: str):
    s = (s or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class Command(BaseCommand):
    help = ("Repoint judge photo_url to self-hosted /static/ portraits and "
            "apply NH justice bios from the localization manifest.")

    def add_arguments(self, parser):
        parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                            help="Path to judge_localization.json.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report changes without writing.")

    def handle(self, *args, manifest, dry_run, **options):
        path = Path(manifest)
        if not path.exists():
            raise CommandError("Manifest not found: %s" % path)
        entries = json.loads(path.read_text(encoding="utf-8"))

        updated = photos = bios = missing = 0
        for e in entries:
            try:
                j = Judge.objects.select_related("state").get(
                    state__code=e["state"], slug=e["slug"])
            except Judge.DoesNotExist:
                self.stderr.write("  no Judge %s/%s -- skipping" % (e["state"], e["slug"]))
                missing += 1
                continue

            fields = []
            if e.get("photo"):
                url = "https://%s.docketdrift.com/static/%s" % (j.state.slug, e["photo"])
                if j.photo_url != url:
                    j.photo_url = url
                    fields.append("photo_url")
                    photos += 1

            is_nh_bio = bool(e.get("bio_summary"))
            if is_nh_bio and j.bio_summary != e["bio_summary"]:
                j.bio_summary = e["bio_summary"]; fields.append("bio_summary"); bios += 1
            if e.get("bio_url") and j.bio_url != e["bio_url"]:
                j.bio_url = e["bio_url"]; fields.append("bio_url")
            if e.get("role") in Judge.Role.values and j.role != e["role"]:
                j.role = e["role"]; fields.append("role")
            appt = _parse_date(e.get("appointment_date", ""))
            if appt and j.appointment_date != appt:
                j.appointment_date = appt; fields.append("appointment_date")
            if is_nh_bio:  # the manifest's NH entries are the seated roster
                if not j.is_currently_seated:
                    j.is_currently_seated = True; fields.append("is_currently_seated")
                if j.status != Judge.Status.ACTIVE:
                    j.status = Judge.Status.ACTIVE; fields.append("status")

            if fields:
                updated += 1
                if not dry_run:
                    j.save(update_fields=fields)
                self.stdout.write("  %s/%s <- %s" % (e["state"], e["slug"], ", ".join(fields)))

        self.stdout.write(self.style.SUCCESS(
            "Done. entries=%d updated=%d photos=%d bios=%d missing=%d%s"
            % (len(entries), updated, photos, bios, missing,
               " (dry-run; no writes)" if dry_run else "")))
