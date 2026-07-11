"""Score each opinion against every embedded Tag, write TagSuggestion rows.

For every opinion that:
  - has an ``embedding`` (i.e. was processed by ``embed_opinions``),
  - has no existing ``TagSuggestion`` rows (idempotent skip),

compute cosine similarity to all embedded ``Tag`` rows. The top
``TAG_SUGGESTION_TOP_N`` matches above ``TAG_SUGGESTION_REVIEW_THRESHOLD``
become ``TagSuggestion`` rows. Anything above
``TAG_SUGGESTION_AUTO_APPLY_THRESHOLD`` gets the tag attached to the
opinion automatically (status=AUTO_APPLIED) so the maintainer doesn't
have to click through a guaranteed-yes.

Implementation: one MariaDB ``VEC_DISTANCE_COSINE`` query per tag against
the full Opinion.embedding VECTOR column, filtered server-side to rows
above the review threshold. ~30-80ms per tag against 60K rows; 31 tags
runs in 2-3 seconds total. The cross product (opinion x tag) lives in
Python memory but is sparse (only above-threshold pairs survive) so
peak RAM stays under ~50MB even for the full corpus.

We deliberately don't pull numpy into the dependency chain -- it has a
BLAS-linkage problem on NFSN's FreeBSD that no available wheel resolves,
and MariaDB's VEC_DISTANCE_COSINE is already wired up for semantic
search anyway. Same primitive, no new failure mode.

Local SQLite dev no-ops because the VECTOR column and VEC_DISTANCE_COSINE
function don't exist there. Run this command on production.

Idempotent: re-runs skip opinions that already have any TagSuggestion
row, so cron can run nightly without duplicating work. To re-score a
specific opinion, delete its TagSuggestion rows first and re-run.

Usage::

    python manage.py suggest_tags                # full corpus pass
    python manage.py suggest_tags --state NH     # one state's courts only
    python manage.py suggest_tags --limit 500    # smoke test
    python manage.py suggest_tags --dry-run      # score but don't write
    python manage.py suggest_tags --rescore-all  # ignore the idempotent skip
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from opinions.models import Court, Opinion, Tag, TagSuggestion


class Command(BaseCommand):
    help = "Score opinions against tags via cosine similarity; write TagSuggestion rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N opinions (smoke-test convenience).",
        )
        parser.add_argument(
            "--state",
            type=str,
            default=None,
            help=(
                "Restrict scoring to one state's courts (USPS code, e.g. NH). "
                "Matches the per-state idiom of embed_opinions / extract_statutes; "
                "keeps a run surgical to the states you actually want to tag "
                "instead of dragging every un-scored opinion corpus-wide."
            ),
        )
        parser.add_argument(
            "--rescore-all",
            action="store_true",
            help=(
                "Ignore the idempotent skip and re-score every embedded opinion. "
                "Existing TagSuggestion rows are kept (unique_together prevents "
                "duplicates); new tags or shifted thresholds will surface new rows."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute suggestions + counts but don't write or auto-apply tags.",
        )
        parser.add_argument(
            "--review-threshold",
            type=float,
            default=None,
            help=(
                "Override TAG_SUGGESTION_REVIEW_THRESHOLD for this run. Useful for "
                "calibration sweeps without editing settings + restarting gunicorn."
            ),
        )
        parser.add_argument(
            "--auto-apply-threshold",
            type=float,
            default=None,
            help=(
                "Override TAG_SUGGESTION_AUTO_APPLY_THRESHOLD for this run."
            ),
        )
        parser.add_argument(
            "--top-n",
            type=int,
            default=None,
            help="Override TAG_SUGGESTION_TOP_N for this run.",
        )

    def handle(self, *args, limit, state, rescore_all, dry_run, review_threshold,
               auto_apply_threshold, top_n, **options):
        if connection.vendor != "mysql":
            raise CommandError(
                f"suggest_tags requires MariaDB / MySQL "
                f"(got {connection.vendor!r}). The Opinion.embedding VECTOR column "
                "and VEC_DISTANCE_COSINE function don't exist on SQLite. "
                "Run this command on production."
            )

        # This is a batch scan, not a web request. Each per-tag
        # VEC_DISTANCE_COSINE pass over a 20-40K-row corpus legitimately runs
        # tens of seconds and can spike past the 25s web cap (settings'
        # init_command) under daytime DB contention -> errno 1969 mid-scan.
        # Lift the cap for THIS command's connection only; web traffic keeps
        # the 25s ceiling via gunicorn's own pooled connections. Same pattern
        # the long migrations use; the per-scan SET STATEMENT below is a
        # belt-and-suspenders guard in case a reconnect re-applies the cap.
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_statement_time = 0")

        # CLI flags override settings; settings provide defaults.
        if review_threshold is None:
            review_threshold = getattr(settings, "TAG_SUGGESTION_REVIEW_THRESHOLD", 0.25)
        if auto_apply_threshold is None:
            auto_apply_threshold = getattr(settings, "TAG_SUGGESTION_AUTO_APPLY_THRESHOLD", 0.40)
        if top_n is None:
            top_n = getattr(settings, "TAG_SUGGESTION_TOP_N", 5)

        if not (0.0 <= review_threshold <= auto_apply_threshold <= 1.0):
            raise CommandError(
                f"Thresholds out of order: review={review_threshold}, "
                f"auto_apply={auto_apply_threshold}. Need 0 <= review <= auto_apply <= 1."
            )

        # Cosine *distance* = 1 - cosine *similarity*. MariaDB's VEC_DISTANCE_COSINE
        # returns the distance, so we flip the thresholds: lower distance = better
        # match, distance < review_distance means similarity > review_threshold.
        review_distance = 1.0 - review_threshold
        auto_apply_distance = 1.0 - auto_apply_threshold

        # Optional per-state scope. Resolve the state's court IDs to a literal
        # `AND court_id IN (...)` fragment (ids are trusted DB ints, so inlining
        # them is injection-safe and keeps the fragment reusable across the
        # count + candidate-id queries below). A handful of courts per state, so
        # the IN-list stays tiny.
        court_filter_sql = ""
        if state:
            code = state.strip().upper()
            court_ids = list(
                Court.objects.filter(state__code=code).values_list("id", flat=True)
            )
            if not court_ids:
                raise CommandError(
                    f"No courts found for state {code!r}. Is the state code correct "
                    "and has it been seeded/ingested?"
                )
            court_filter_sql = " AND court_id IN (" + ",".join(
                str(c) for c in court_ids
            ) + ")"

        # (1) Build the opinion candidate set.
        with connection.cursor() as cursor:
            base_where = "embedding IS NOT NULL"
            if not rescore_all:
                base_where += (
                    " AND id NOT IN ("
                    "  SELECT opinion_id FROM opinions_tagsuggestion"
                    ")"
                )
            base_where += court_filter_sql
            cursor.execute(
                f"SELECT COUNT(*) FROM opinions_opinion WHERE {base_where}"
            )
            total_in_scope = cursor.fetchone()[0]

        if limit:
            total_in_scope = min(total_in_scope, limit)

        if total_in_scope == 0:
            self.stdout.write(self.style.SUCCESS(
                "All embedded opinions already have suggestions. Nothing to do."
            ))
            return

        # (2) Build the set of "in-scope opinion IDs" we're willing to score
        # so the per-tag distance scans can filter on it cheaply. For full
        # corpus runs without --limit, we skip the IN-list and let the WHERE
        # clause stand on its own.
        in_scope_ids: list[int] | None = None
        if limit or not rescore_all or state:
            with connection.cursor() as cursor:
                lim_clause = "LIMIT %s" if limit else ""
                params = [limit] if limit else []
                cursor.execute(
                    f"SELECT id FROM opinions_opinion WHERE {base_where} ORDER BY id {lim_clause}",
                    params,
                )
                in_scope_ids = [row[0] for row in cursor.fetchall()]

        # (3) Load embedded tags. JSONField yields a Python list of floats;
        # we re-serialize each tag's vector to a JSON string to pass to
        # Vec_FromText().
        tags = list(Tag.objects.exclude(embedding__isnull=True).order_by("slug"))
        if not tags:
            raise CommandError(
                "No tags have embeddings yet. Run `python manage.py embed_tags` first."
            )

        self.stdout.write(self.style.SUCCESS(
            f"Scoring {total_in_scope:,} opinion{'' if total_in_scope == 1 else 's'} "
            f"against {len(tags)} embedded tag{'' if len(tags) == 1 else 's'}.  "
            f"review={review_threshold}  auto={auto_apply_threshold}  top_n={top_n}"
            + ("  (DRY RUN)" if dry_run else "")
            + ("  (RESCORE ALL)" if rescore_all else "")
        ))

        # (4) For each tag, scan the opinion set with VEC_DISTANCE_COSINE.
        # Server-side filter on distance keeps the cross-product small --
        # only above-review-threshold pairs cross the wire to Python.
        # Accumulate per-opinion candidate lists.
        candidates: dict[int, list[tuple[int, float]]] = defaultdict(list)
        run_started = time.time()

        for i, tag in enumerate(tags, 1):
            tag_vec_json = json.dumps(tag.embedding)

            sql = [
                "SELECT id,",
                "       VEC_DISTANCE_COSINE(embedding, Vec_FromText(%s)) AS dist",
                "FROM opinions_opinion",
                "WHERE embedding IS NOT NULL",
            ]
            params: list = [tag_vec_json]
            if in_scope_ids is not None:
                # Bound the IN-list to keep the query plan tight. For the
                # full corpus, this list is up to 60K ints -- still a
                # reasonable IN clause for MariaDB.
                placeholders = ",".join(["%s"] * len(in_scope_ids))
                sql.append(f"  AND id IN ({placeholders})")
                params.extend(in_scope_ids)
            sql.append("HAVING dist < %s")
            params.append(review_distance)

            t0 = time.time()
            with connection.cursor() as cursor:
                # SET STATEMENT self-binds this one scan to the uncapped limit
                # even if a reconnect reset the session cap back to 25s.
                cursor.execute("SET STATEMENT max_statement_time=0 FOR\n" + "\n".join(sql), params)
                for opinion_id, dist in cursor.fetchall():
                    score = 1.0 - float(dist)
                    candidates[opinion_id].append((tag.pk, score))
            self.stdout.write(
                f"  [{i:>2}/{len(tags)}] {tag.slug:<32}  "
                f"hits={len(candidates):>5,}  "
                f"({(time.time()-t0)*1000:>4.0f}ms)"
            )

        scan_elapsed = time.time() - run_started

        # (5) For each opinion: keep top-N candidates above review, write
        # TagSuggestion rows (idempotent via unique_together), auto-apply
        # the high-confidence ones.
        suggestions_pending = 0
        suggestions_auto = 0
        tags_auto_applied = 0
        opinions_touched = 0

        # When --rescore-all is set, an opinion may already have suggestions;
        # we still emit new rows for unseen (opinion, tag) pairs and let
        # the unique constraint dedupe the rest.
        now = timezone.now()
        write_started = time.time()

        for opinion_id, tag_scores in candidates.items():
            tag_scores.sort(key=lambda t: -t[1])  # descending by score
            chosen = tag_scores[:top_n]
            if not chosen:
                continue
            opinions_touched += 1

            for tag_id, score in chosen:
                if score >= auto_apply_threshold:
                    status = TagSuggestion.Status.AUTO_APPLIED
                    suggestions_auto += 1
                else:
                    status = TagSuggestion.Status.PENDING
                    suggestions_pending += 1
                if dry_run:
                    continue
                _, created = TagSuggestion.objects.get_or_create(
                    opinion_id=opinion_id,
                    tag_id=tag_id,
                    defaults={
                        "confidence": score,
                        "status": status,
                        "reviewed_at": now if status == TagSuggestion.Status.AUTO_APPLIED else None,
                        "reviewed_by": "auto-applied" if status == TagSuggestion.Status.AUTO_APPLIED else "",
                    },
                )
                if created and status == TagSuggestion.Status.AUTO_APPLIED:
                    # M2M through-row insert is idempotent via the (opinion,
                    # tag) unique constraint Django creates for the
                    # ManyToManyField.
                    Opinion.tags.through.objects.get_or_create(
                        opinion_id=opinion_id,
                        tag_id=tag_id,
                    )
                    tags_auto_applied += 1

        write_elapsed = time.time() - write_started
        total_elapsed = time.time() - run_started

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done in {total_elapsed:.1f}s "
            f"(scan {scan_elapsed:.1f}s + persist {write_elapsed:.1f}s)."
        ))
        self.stdout.write(
            f"  opinions scored:     {total_in_scope:>7,}\n"
            f"  opinions w/ matches: {opinions_touched:>7,}  "
            f"({100.0 * opinions_touched / max(total_in_scope, 1):.1f}%)\n"
            f"  pending suggestions: {suggestions_pending:>7,}\n"
            f"  auto-applied:        {suggestions_auto:>7,}\n"
            f"  tags attached:       {tags_auto_applied:>7,}"
            + ("\n  (DRY RUN -- nothing saved)" if dry_run else "")
        )
