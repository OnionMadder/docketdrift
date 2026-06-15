"""Pre-warm the explore_tags template-context cache for every live state.

The explore-tags sidebar runs ~20 FULLTEXT MATCH-AGAINST COUNTs per
state to size the tag cloud. With the per-state cache cold (no entry
in the persistent FileBasedCache), the first request after expiry
pays the whole cost -- ~2-6 seconds depending on contention. Crawlers
hitting state landings concurrently can multiply this across workers.

This command rebuilds each live state's explore_tags cache entry AND
its state-landing-stats bundle (opinion/judge counts, date range,
distinct tags used) proactively, BEFORE the TTL expires. Schedule it
via NFSN's Scheduled Tasks UI (Manage Site -> Scheduled Tasks) at a
cadence shorter than the cache TTL -- recommended: hourly. Real-user
requests then always hit a warm cache for both.

Idempotent: if the cache is already warm, the build re-uses the same
queries and writes the same value. No DB writes.

Cost: 20 MATCH-COUNTs * N live states once per run. On the current
3-state corpus that's ~60 cheap-when-warm-but-each-2s-when-cold
queries. The command lifts its per-statement timeout (it's background
warming, not a web request) so a contended run -- e.g. one overlapping
the overnight embed window -- completes instead of tripping 1969 and
crashing. ~10-30 seconds wall-clock per run.

Usage::

    python manage.py precompute_explore_tags          # all live states
    python manage.py precompute_explore_tags --state MN  # one state
"""
from __future__ import annotations

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand

from opinions.context_processors import _get_sized_tags
from opinions.models import State


class Command(BaseCommand):
    help = "Pre-warm the explore_tags cache for every live state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state", default=None,
            help="USPS 2-letter state code (e.g. MN). Restrict pre-warming "
                 "to one state. Default: every state with is_live=True.",
        )

    def handle(self, *args, state, **options):
        if state:
            states = list(State.objects.filter(code=state.upper()))
            if not states:
                self.stderr.write(f"State {state.upper()!r} not found.")
                return
        else:
            states = list(State.objects.filter(is_live=True).order_by("code"))

        if not states:
            self.stdout.write("No live states to warm.")
            return

        # Local import: pulls in opinions.views only when the command
        # actually runs, avoiding any import-time cycle at startup.
        from opinions.views import _state_court_ids, _state_landing_stats

        # Lift this connection's per-statement timeout. settings' 25s
        # init_command cap protects gunicorn workers, but the FULLTEXT
        # MATCH-COUNTs and stats-bundle aggregates below are background
        # warming work that can legitimately run longer under DB contention
        # (e.g. an hourly run overlapping the overnight embed window).
        # Without this, a contended query trips 1969 (max_statement_time
        # exceeded) and the whole warmer crashes -- leaving the cache cold,
        # the opposite of the goal. Skipped on non-MariaDB (local SQLite).
        from django.db import connection
        if connection.vendor == "mysql":
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION max_statement_time = 0")

        for s in states:
            # Invalidate then rebuild so the warming run reflects fresh
            # counts even if something else just touched the cache. Warm
            # BOTH per-state caches the landing/apex pages read: the
            # explore-tags cloud AND the state-stats bundle. The stats
            # bundle was previously never pre-warmed, so every TTL expiry
            # hit a real user with the full cold aggregate cost.
            cache.delete(f"explore_tags_sized:{s.code}")
            cache.delete(f"state_landing_stats:{s.code}")
            t0 = time.time()
            sized = _get_sized_tags(s)
            _state_landing_stats(s, _state_court_ids(s))
            elapsed = time.time() - t0
            self.stdout.write(
                f"  {s.code}: {len(sized)} tags + stats warmed in {elapsed:.1f}s"
            )
        self.stdout.write(self.style.SUCCESS("Done."))
