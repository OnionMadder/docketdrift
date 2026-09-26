"""Tell IndexNow search engines about newly ingested opinion pages.

WHY. Bing had barely found us (Bingbot ~2% of crawls; its keyword report
for docketdrift.com showed 26 impressions in a month), and Bing's index is
what ChatGPT's live search reads. A sitemap waits for the engine to come
back around; an IndexNow ping hands it the new URLs the day they appear.
One ping reaches every participating engine (Bing, Yandex, Seznam, Naver).
Google does not participate -- Search Console sitemaps remain its path.

WHAT IS SENT. Public opinion URLs and our key. Nothing about any visitor;
the ping is generated from the database, never from traffic.

SELECTION. New rows are found by ``created_at``, but ``created_at`` has no
index, so filtering on it alone would walk the 2.75GB opinions table. The
fetch is bounded on the indexed ``release_date`` first (``--release-days``,
default 400: wide enough to catch a backfill of recent-year opinions) and
``created_at`` is applied to that small slice.

Re-pinging a URL is harmless, so overlapping windows between runs are fine.

Usage::

    python manage.py indexnow_ping                       # created in last 200h
    python manage.py indexnow_ping --hours 30 --dry-run
    python manage.py indexnow_ping --seed-days 90        # every opinion released
                                                         # in the last 90 days
"""
from __future__ import annotations

import datetime
import re
from collections import defaultdict

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from opinions.models import Court, Opinion

ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS_PER_POST = 10_000  # protocol limit
TIMEOUT = 30


class Command(BaseCommand):
    help = "Ping IndexNow with newly ingested opinion URLs, per state subdomain."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=200,
                            help="Opinions CREATED in this many hours (default 200, "
                                 "i.e. a weekly run with overlap).")
        parser.add_argument("--release-days", type=int, default=400,
                            help="Indexed pre-filter on release_date (default 400).")
        parser.add_argument("--seed-days", type=int, default=None,
                            help="Ignore created_at; send every opinion RELEASED "
                                 "in this many days. For a one-time seed.")
        parser.add_argument("--state", default=None, help="Restrict to one state.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, hours, release_days, seed_days, state, dry_run, **opts):
        key = (getattr(settings, "INDEXNOW_KEY", "") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", key):
            raise CommandError("INDEXNOW_KEY is not set (or malformed) in the environment.")

        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        courts = Court.objects.filter(state__is_live=True).select_related("state")
        if state:
            courts = courts.filter(state_id=state.upper())
        court_host = {c.id: "%s.docketdrift.com" % c.state.slug for c in courts}
        if not court_host:
            raise CommandError("No live courts matched.")

        today = timezone.localdate()
        since_release = today - datetime.timedelta(days=seed_days or release_days)
        since_created = timezone.now() - datetime.timedelta(hours=hours)

        # release_date leads: it is indexed, created_at is not.
        rows = (Opinion.objects
                .filter(release_date__gte=since_release, release_date__lte=today)
                .order_by()
                .values_list("court_id", "case_number", "created_at"))
        by_host = defaultdict(list)
        for court_id, case_number, created in rows:
            host = court_host.get(court_id)
            if not host or not case_number:
                continue
            if seed_days is None and (created is None or created < since_created):
                continue
            path = reverse("opinions:detail", kwargs={"case_number": case_number})
            by_host[host].append("https://%s%s" % (host, path))

        if not by_host:
            self.stdout.write("nothing to send")
            return

        failures = []
        for host in sorted(by_host):
            urls = sorted(set(by_host[host]))
            self.stdout.write("%s: %d url(s)%s" % (host, len(urls), " [dry run]" if dry_run else ""))
            if dry_run:
                for u in urls[:3]:
                    self.stdout.write("   " + u)
                continue
            for i in range(0, len(urls), MAX_URLS_PER_POST):
                payload = {
                    "host": host,
                    "key": key,
                    "keyLocation": "https://%s/indexnow-key.txt" % host,
                    "urlList": urls[i:i + MAX_URLS_PER_POST],
                }
                try:
                    r = requests.post(ENDPOINT, json=payload, timeout=TIMEOUT)
                    status = r.status_code
                except requests.RequestException as e:
                    status = "error: %s" % type(e).__name__
                # 200 = accepted, 202 = accepted pending key validation.
                ok = status in (200, 202)
                self.stdout.write("   batch %d: %s" % (i // MAX_URLS_PER_POST + 1, status))
                if not ok:
                    failures.append("%s batch %d -> %s" % (host, i // MAX_URLS_PER_POST + 1, status))

        # Non-zero exit so the scheduled task's email surfaces a rejected key
        # (403) or malformed batch (422) instead of reporting success.
        if failures:
            raise CommandError("IndexNow rejected: " + "; ".join(failures))
