"""Public-facing views for DocketDrift.

``opinions.middleware.StateRouterMiddleware`` attaches ``request.state`` based
on the incoming subdomain; the views below switch between:

- the apex view (state picker) when the subdomain doesn't resolve to a State,
- the per-state landing (recent opinions + search) when it does,
- a single-opinion detail page rendered under the state context,
- an about page describing methodology + data sources.

All rendering goes through the ``opinions/*.html`` templates in
``opinions/templates/opinions/``; the look is the maddervramsey-derived
dark/neon design system loaded via the base template.
"""
import re
from datetime import date, timedelta

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import connection, models
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import cache_control, cache_page
from django.views.decorators.vary import vary_on_headers

from opinions.models import Judge, Opinion, State, StatuteCitation, Tag


# Cache-Control max-age budgets, in seconds. Set on views below via the
# ``@cache_control`` decorator. Cloudflare (when sitting in front of the
# origin) reads these and serves cached responses from the edge,
# absorbing repeat-traffic without touching NFSN. Budgets are deliberately
# conservative -- a new opinion ingest only changes detail pages it
# creates (fresh URLs cache-miss naturally), and the existing pages stay
# valid until evicted.
CACHE_SEC_DETAIL = 3600          # opinion + judge detail (1 hour)
CACHE_SEC_DOSSIER_LIST = 3600    # current-judges (1 hour)
CACHE_SEC_HOME = 900             # state home (15 min -- new opinions land via cron)
CACHE_SEC_STATIC = 86400         # about / privacy / support (24 hours)
CACHE_SEC_ROBOTS = 86400         # /robots.txt (24 hours)
CACHE_SEC_STATE_STATS = 7200     # state-landing stats bundle (2 hr). MUST
# exceed the hourly precompute_explore_tags warming interval -- at 30 min it
# expired mid-hour and real requests paid the cold rebuild (the recurring
# state-landing slowness). Counts change only on weekly ingest + editor
# activity, so 2 hr staleness is fine.


# MariaDB's default ft_min_word_len is 4. Shorter queries can't match via
# FULLTEXT so we fall back to LIKE for them.
FULLTEXT_MIN_QUERY_LEN = 4


# Page size when the user has filtered or searched (power-user mode).
# 60K MN opinions / 50 per page = ~1,200 pages -- comfortable to navigate
# once filtered down to a topic.
HOME_PAGE_SIZE = 50

# Page size on the default landing (no filter/search) -- casual visitor.
# Recent activity surface; search box is the way to go deeper.
HOME_LANDING_SIZE = 10

# Default date window for searches. ?years=all overrides to the full
# corpus (back to 1930); ?years=N overrides to a specific window. 10
# years is what 95% of casual users actually want -- the deep archive
# is one click away.
DEFAULT_SEARCH_YEARS = 10


# Per-state explanation of why coverage might lag real-time. Shown on
# the state-landing page when the most recent opinion is > the
# STALE_DAYS_THRESHOLD. Keeps disclosure honest + specific.
COVERAGE_LAG_NOTES = {
    "NH": (
        "We depend on CourtListener's ingestion of New Hampshire Supreme Court "
        "opinions, which typically lags real-time by several weeks to several "
        "months. Direct ingestion from <code>courts.nh.gov</code> is blocked by "
        "the court site's CDN, so we don't have a faster channel yet. "
        "Updated coverage lands automatically as CourtListener catches up."
    ),
    "AZ": (
        "Coverage depends on CourtListener's ingestion of Arizona appellate "
        "opinions, which can lag real-time by several weeks. Direct ingestion "
        "from the Arizona court system would require additional integration; "
        "until then, updated coverage lands automatically as CourtListener catches up."
    ),
    "MN": (
        "Our weekly scheduled job is the primary source for new Minnesota "
        "appellate opinions. If you're seeing a gap longer than the normal "
        "Monday/Wednesday release schedule, the ingest job may have failed; "
        "please email <a href=\"mailto:hello@docketdrift.com\">hello@docketdrift.com</a>."
    ),
}
DEFAULT_COVERAGE_LAG_NOTE = (
    "We depend on CourtListener's ingestion cadence for this state's opinions, "
    "which can lag real-time by weeks to months. Updated coverage lands "
    "automatically as CourtListener catches up."
)
STALE_DAYS_THRESHOLD = 30


def _state_court_ids(state):
    """Court PKs for a state, resolved in Python.

    Lets queries filter on the indexed ``court_id`` instead of JOINing
    opinions -> courts -> states (the court table is a handful of rows per
    state). Centralizes the idiom that was copy-pasted across home,
    opinion_list, and the sitemaps.
    """
    return list(state.courts.values_list("id", flat=True))


def _state_landing_stats(state, court_ids):
    """Cached bundle of the slow per-state scalar aggregates.

    Opinion count, judge counts, release-date range, and distinct tags
    used -- each a full-index scan that competes with the embed pipeline
    on the same MariaDB instance. Built in ONE shot and cached as a single
    entry so neither a real request nor the precompute warmer recomputes
    them per call. ``precompute_explore_tags`` calls this too, so the
    bundle is genuinely pre-warmed for real users (it previously was not,
    despite a comment claiming so).
    """
    key = f"state_landing_stats:{state.code}"
    bundle = cache.get(key)
    if bundle is None:
        state_opinions = Opinion.objects.filter(court_id__in=court_ids)
        judges_qs = Judge.objects.filter(state=state)
        bundle = {
            "total_opinions": state_opinions.values("pk").count(),
            "total_judges": judges_qs.count(),
            "currently_seated": judges_qs.filter(is_currently_seated=True).count(),
            "date_range": state_opinions.aggregate(
                first=models.Min("release_date"),
                last=models.Max("release_date"),
            ),
            "total_tags_used": (
                Tag.objects.filter(opinions__court_id__in=court_ids)
                .distinct().count()
            ),
        }
        cache.set(key, bundle, CACHE_SEC_STATE_STATS)
    return bundle


@cache_control(public=True, max_age=CACHE_SEC_HOME)
def home(request):
    """Apex state-picker OR per-state landing page.

    Three branches:

    - ``request.state is None`` (no subdomain match): render the apex
      state picker.
    - State subdomain root with NO search/filter params: render the
      state landing page (hero + stats + recent activity + browse cards).
      This is what visitors hitting ``mn.docketdrift.com`` see -- a
      brand-forward landing, not a raw opinion list.
    - State subdomain root WITH search/filter params: redirect to
      ``/opinions/?...`` so the landing URL stays the landing URL even
      as the search box submits from any page.
    """
    state = getattr(request, "state", None)

    if state is None:
        live = list(State.objects.filter(is_live=True).order_by("name"))
        for s in live:
            # Cached bundle (court_id-resolved) instead of an uncached
            # opinions -> courts -> states JOIN-COUNT per state on the
            # busiest page. Shared with the per-state landing and warmed
            # by precompute_explore_tags.
            s.opinion_count = _state_landing_stats(
                s, _state_court_ids(s)
            )["total_opinions"]
        return render(request, "opinions/apex.html", {
            "states": live,
            "active_nav": "opinions",
            "search_q": (request.GET.get("q") or "").strip(),
        })

    # Search/filter params bypass the landing -- redirect to the
    # opinion-list view with the same querystring.
    if any(p in request.GET for p in ("q", "disposition", "page", "years")):
        from django.urls import reverse
        return redirect(f"{reverse('opinions:opinion_list')}?{request.GET.urlencode()}")

    # State landing.
    court_ids = _state_court_ids(state)
    state_opinions = Opinion.objects.filter(court_id__in=court_ids)

    # Slow scalar aggregates come from one cached bundle (also pre-warmed
    # by precompute_explore_tags) rather than being recomputed per request.
    stats = _state_landing_stats(state, court_ids)
    total_opinions = stats["total_opinions"]
    total_judges = stats["total_judges"]
    currently_seated = stats["currently_seated"]
    date_range = stats["date_range"]
    total_tags_used = stats["total_tags_used"]
    total_tags_available = Tag.objects.count()

    # Recent-opinions card. Anchor the window to the corpus's NEWEST
    # opinion, not to today: a fixed today-180d window rendered an EMPTY
    # card for any state whose latest opinion is >180d old (exactly the
    # case the staleness banner exists for) while the page still claimed N
    # opinions indexed. Anchoring to date_range["last"] keeps the window
    # bounded -- so MariaDB walks the indexed release_date DESC instead of
    # full-scan-then-sort -- AND guarantees it contains the newest rows.
    # Defer the 50-100KB TEXT columns; the card renders only docket /
    # court / disposition / title.
    last_filed = date_range.get("last") if date_range else None
    if last_filed:
        latest_window = last_filed - timedelta(days=180)
        latest_opinions = list(
            state_opinions.defer("raw_text", "html_content")
            .filter(release_date__gte=latest_window)
            .select_related("court")
            .order_by("-release_date")[:5]
        )
    else:
        latest_opinions = []

    # Coverage staleness: surface an honest disclosure when the most recent
    # opinion in our corpus is more than STALE_DAYS_THRESHOLD old. Reason
    # text differs per state because the cause differs (NH/AZ: CL lag
    # because we can't scrape the court site directly; MN: should rarely
    # fire because we ingest weekly via direct CL).
    coverage_note = None
    if last_filed:
        from django.utils.safestring import mark_safe
        days_stale = (date.today() - last_filed).days
        if days_stale > STALE_DAYS_THRESHOLD:
            reason_html = COVERAGE_LAG_NOTES.get(state.code, DEFAULT_COVERAGE_LAG_NOTE)
            coverage_note = {
                "days_stale": days_stale,
                "last_filed": last_filed,
                "reason_html": mark_safe(reason_html),
            }

    return render(request, "opinions/state_landing.html", {
        "state": state,
        "total_opinions": total_opinions,
        "total_judges": total_judges,
        "currently_seated": currently_seated,
        "latest_opinions": latest_opinions,
        "date_range": date_range,
        "total_tags_used": total_tags_used,
        "total_tags_available": total_tags_available,
        "coverage_note": coverage_note,
        "active_nav": "home",
    })


_SNIPPET_WINDOW = 240   # total characters per snippet
_SNIPPET_PAD = 80       # characters of context on each side of the match


def _attach_match_snippets(opinions, query):
    """Attach an HTML-highlighted ``snippet_html`` to each opinion.

    For each opinion in the list, find the first case-insensitive
    occurrence of ``query`` in raw_text via MariaDB's ``INSTR`` and
    return a SUBSTRING window around it -- all done DB-side so we don't
    pump 100KB of raw_text across the wire per result row just to
    extract a 240-char snippet.

    The returned snippet is HTML-escaped, then every (case-insensitive)
    occurrence of the query inside that window is wrapped in ``<mark>``
    tags. Marked safe so the template can render it inline. Opinions
    where the query isn't present in raw_text (e.g. semantic-search-
    only matches) get an empty string -- the template treats that as
    "no snippet to show".

    Skips silently on local SQLite dev (no MariaDB SUBSTRING/INSTR
    in the test setup). The template guards on ``op.snippet_html``
    truthiness, so an empty annotation just hides the snippet row.

    DEFENSIVE: the whole body runs inside a try/except. Snippets are a
    UX nice-to-have; a query timeout or transient DB error here should
    NOT 500 the entire search page. On failure we log + return,
    leaving each opinion's ``snippet_html`` unset (template hides the
    row).
    """
    if connection.vendor != "mysql" or not query or not opinions:
        return

    ids = [op.pk for op in opinions]
    placeholders = ",".join(["%s"] * len(ids))
    # We deliberately do NOT call LOWER(raw_text) here. The table's
    # collation (utf8mb4_unicode_ci) makes INSTR case-insensitive
    # already, and pre-LOWERing a 50-100KB raw_text per row was
    # costing 5-10s under embed contention. Native INSTR against the
    # collation is one operation per row instead of three.
    sql = f"""
        SELECT id,
               SUBSTRING(
                   raw_text,
                   GREATEST(1, INSTR(raw_text, %s) - %s),
                   %s
               ) AS snippet
        FROM opinions_opinion
        WHERE id IN ({placeholders})
          AND INSTR(raw_text, %s) > 0
    """
    params = [query, _SNIPPET_PAD, _SNIPPET_WINDOW, *ids, query]
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            snippets = {row[0]: row[1] or "" for row in cursor.fetchall()}
    except BaseException:
        # Catch BaseException, not Exception. NFSN's SSL socket raises
        # KeyboardInterrupt on EINTR (a BaseException), which would
        # otherwise escape this handler and 500 the whole search page --
        # the exact footgun documented in CLAUDE.md. Snippets are a UX
        # nice-to-have; a failure here (also: MAX_STATEMENT_TIME tripped,
        # transient connection drop) must never take down search results.
        import logging
        logging.getLogger(__name__).warning(
            "_attach_match_snippets failed; returning without snippets",
            exc_info=True,
        )
        return

    if not snippets:
        return

    import html as _html
    import re as _re
    from django.utils.safestring import mark_safe

    # Build a case-insensitive regex that matches the literal query
    # text. We re.escape so phrase queries with regex metacharacters
    # (parens, dots, brackets) match literally.
    query_re = _re.compile(_re.escape(query), _re.IGNORECASE)

    for op in opinions:
        raw_snippet = snippets.get(op.pk, "")
        if not raw_snippet:
            op.snippet_html = ""
            continue
        # Collapse internal whitespace + strip edges so the snippet
        # reads as a single tight line rather than column-broken
        # PDF-extracted raw_text.
        cleaned = _re.sub(r"\s+", " ", raw_snippet).strip()
        # Add ellipses to signal mid-text truncation when applicable
        # (always true unless the snippet starts at offset 1).
        ellipsis = "&hellip; "
        escaped = _html.escape(cleaned, quote=False)
        highlighted = query_re.sub(
            lambda m: f"<mark>{_html.escape(m.group(0), quote=False)}</mark>",
            escaped,
        )
        op.snippet_html = mark_safe(ellipsis + highlighted + " &hellip;")


@cache_control(public=True, max_age=CACHE_SEC_HOME)
def opinion_list(request):
    """The full opinion browse/search view. State-scoped.

    What was previously the state landing -- the keyword + semantic
    search results, the dig-deeper prompt, the pagination. Moved here
    so the bare state URL can be a proper landing page and ``/opinions/``
    is the explicit "show me the actual list" URL the search box
    submits to.
    """
    state = getattr(request, "state", None)
    if state is None:
        return redirect("/")

    search_q = (request.GET.get("q") or "").strip()
    disp_filter = (request.GET.get("disposition") or "").strip().lower()

    years_param = (request.GET.get("years") or "").strip().lower()
    if years_param == "all":
        years_back: int | None = None
    elif years_param.isdigit() and 1 <= int(years_param) <= 100:
        years_back = int(years_param)
    else:
        years_back = DEFAULT_SEARCH_YEARS

    # Pre-resolve court IDs for this state -- avoids an INNER JOIN
    # on every queryset evaluation (especially the paginator's COUNT(*)).
    # Was running 30s+ live against the now-tripled corpus; goes to
    # ~100ms with the FK-index-only count via court_id__in.
    court_ids = _state_court_ids(state)

    qs = (
        Opinion.objects.filter(court_id__in=court_ids)
        .select_related("court")
        .order_by("-release_date")
    )

    # Date window applies ONLY to active searches/filters. The default
    # landing always reflects the whole corpus so the "60K opinions
    # indexed, going back to the 1930s" message stays honest. Computed
    # once and reused for both the keyword qs and the semantic-search
    # SQL filter so the two surfaces always match. We use timedelta
    # rather than .replace(year=...) to dodge the Feb-29-in-non-leap-
    # year edge case.
    date_cutoff = None
    if years_back is not None and (search_q or disp_filter):
        from datetime import date, timedelta
        date_cutoff = date.today() - timedelta(days=365 * years_back)
        qs = qs.filter(release_date__gte=date_cutoff)
    if search_q:
        # Statute-cite shortcut. If the query parses as a statute citation
        # under the current state's grammar, redirect straight to the
        # statute page. Saves the user a hop through the search results
        # for the most-common "I have a citation, where's the source"
        # path. Patterns recognized:
        #   MN:  "Minn. Stat. § 609.185"
        #   NH:  "RSA 159-B:1"
        #   AZ:  "A.R.S. § 13-1103"
        # The statute parser itself decides what counts -- same code that
        # populated the statute pages now also routes the search box.
        from opinions.parsing.statutes import extract_statutes as _parse_statutes
        cites = _parse_statutes(state.code, search_q) if state else []
        if cites:
            from django.urls import reverse
            return redirect(reverse(
                "opinions:statute_detail",
                kwargs={"reference": cites[0].reference_slug},
            ))

        use_fulltext = (
            connection.vendor == "mysql"
            and len(search_q) >= FULLTEXT_MIN_QUERY_LEN
        )
        # Route the query down the FAST path that matches its shape.
        # Mixing case_number LIKE (leading-%, defeats the index, full
        # scan) with FULLTEXT MATCH AGAINST via OR forced the planner
        # into a full-table-scan plan on the 60K-row corpus -- a single
        # "Fourth Amendment" COUNT took 75 seconds and saturated
        # gunicorn's worker threads. Splitting by shape gives each
        # query the index it actually needs.
        #
        # Docket-like queries (case-number-style: starts with a letter
        # or digit, contains a hyphen, no spaces, short) get a pure
        # case_number scan. Everything else gets FULLTEXT-only.
        is_docket_like = (
            len(search_q) <= 24
            and " " not in search_q
            and ("-" in search_q or re.match(r"^[A-Za-z]\d", search_q))
        )
        if is_docket_like:
            # case_number is indexed; .icontains uses %...% but the
            # column is small so the scan is still fast.
            qs = qs.filter(case_number__icontains=search_q)
        elif use_fulltext:
            # FULLTEXT-only. The index handles MATCH AGAINST in
            # milliseconds even on raw_text columns. Phrase-quoted in
            # BOOLEAN MODE so multi-word queries match as exact phrases.
            qs = qs.extra(
                where=[
                    "MATCH(opinions_opinion.raw_text, opinions_opinion.title) "
                    "AGAINST (%s IN BOOLEAN MODE)"
                ],
                params=[f'"{search_q}"'],
            )
        else:
            qs = qs.filter(
                Q(case_number__icontains=search_q)
                | Q(title__icontains=search_q)
                | Q(raw_text__icontains=search_q)
            )
    if disp_filter:
        qs = qs.filter(disposition_bucket=disp_filter)

    is_filtered = bool(search_q or disp_filter)

    if is_filtered:
        # NoJoinCountPaginator: the paginator's ``.count`` SQL drops the
        # ``.select_related("court")`` JOIN that the rendering pass needs
        # so the COUNT(*) stays a single-table indexed PK scan. The
        # FULLTEXT MATCH() filter survives (``.extra()`` WHERE clauses
        # are preserved by ``.values("pk")``). Public search hit the same
        # 30s-COUNT trap that admin opinion changelist did; this is the
        # same fix on the public surface.
        #
        # Defer the giant TEXT columns on the rendering pass too -- the
        # search-results table only displays docket / court / disposition /
        # title. The match-context snippet comes from a separate
        # SUBSTRING query (see _attach_match_snippets below) that pulls
        # ~240 bytes per opinion instead of the full 50-100KB raw_text.
        from opinions.paginators import NoJoinCountPaginator
        qs_render = qs.defer("raw_text", "html_content")
        paginator = NoJoinCountPaginator(qs_render, HOME_PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page", 1))
        opinions = page_obj.object_list
        total_count = paginator.count
    else:
        # Default landing: just the most recent N. No paginator object so
        # the template knows we're in landing mode. Even here we count
        # via ``.values("pk")`` to keep the join out of the COUNT. Defer
        # the body columns -- list views don't render them.
        #
        # Anchor the recency window to the corpus's NEWEST opinion rather
        # than to today. A fixed today-365d window rendered an EMPTY list
        # under a non-zero "N opinions indexed" header for any state whose
        # latest opinion is >365d old. Anchoring to the corpus max keeps
        # the window bounded (so MariaDB walks the indexed release_date
        # DESC instead of full-scan-then-sort) but can't outrun a stale
        # corpus. One cheap indexed MAX drives it.
        page_obj = None
        # `timedelta` is imported locally inside the search branch above,
        # which makes it a function-local name everywhere in opinion_list --
        # so on this no-search path it is otherwise unbound. Import it here
        # too. (See module TODO: hoist datetime imports to module scope.)
        from datetime import timedelta
        # Reuse the cached state-stats bundle for BOTH the whole-corpus
        # count and the recency anchor -- they're the same values home()
        # already caches. Computing them here as two uncached aggregates
        # (a full-corpus COUNT and a MAX) on every default-landing request
        # was tripping max_statement_time under crawler load. The count is
        # still the WHOLE corpus, so the "X opinions indexed" header stays
        # honest even though the list is windowed.
        stats = _state_landing_stats(state, court_ids)
        total_count = stats["total_opinions"]
        landing_anchor = (stats.get("date_range") or {}).get("last")
        if landing_anchor:
            landing_window = landing_anchor - timedelta(days=365)
            opinions = list(
                qs.defer("raw_text", "html_content")
                .filter(release_date__gte=landing_window)[:HOME_LANDING_SIZE]
            )
        else:
            opinions = []

    # Snippet generation. For keyword searches, pull a ~240-char window
    # around the FIRST occurrence of the query in each opinion's
    # raw_text and highlight the match with <mark> tags. The SUBSTRING
    # runs DB-side so we don't ship 100KB of raw_text per row across
    # the wire just to extract 240 bytes. Renders as the "match context"
    # row below each result so a paste-the-quote search shows WHERE in
    # which opinion the phrase appears.
    if search_q and opinions:
        _attach_match_snippets(opinions, search_q)

    # Semantic search: when the user has typed a query, also run a
    # vector-similarity search alongside the keyword/FULLTEXT one and
    # surface a separate "semantically similar" block in the template.
    # Cached per-query, so repeat searches cost nothing. Skips silently
    # on local SQLite (no VECTOR column) and when Voyage isn't configured.
    semantic_opinions = []
    # Same crawler guard as opinion_detail: a bot crawling search-result
    # URLs shouldn't trigger the Voyage embed + VEC scan.
    from opinions.middleware import request_is_crawler
    if search_q and not disp_filter and not request_is_crawler(request):
        from opinions.semantic import get_query_embedding, search_similar_opinions
        embedding = get_query_embedding(search_q)
        if embedding:
            similar_ids = search_similar_opinions(
                embedding, state, limit=10, date_cutoff=date_cutoff,
            )
            if similar_ids:
                # Preserve the cosine-distance ordering returned by the
                # similarity query (it's not the same as release_date desc).
                ordering = {pk: i for i, pk in enumerate(similar_ids)}
                fetched = list(
                    Opinion.objects.filter(pk__in=similar_ids)
                    .select_related("court")
                )
                fetched.sort(key=lambda op: ordering.get(op.pk, 999))
                # Hide opinions that already appeared in the keyword
                # results so we don't show the same row twice.
                keyword_ids = {op.pk for op in opinions}
                semantic_opinions = [op for op in fetched if op.pk not in keyword_ids]

    # Find the human-readable label of the active disposition filter for the banner.
    disp_label = ""
    if disp_filter:
        from opinions.context_processors import DISPOSITION_BUCKETS
        for slug, label in DISPOSITION_BUCKETS:
            if slug == disp_filter:
                disp_label = label
                break

    return render(request, "opinions/state_home.html", {
        "state": state,
        "opinions": opinions,
        "semantic_opinions": semantic_opinions,
        "page_obj": page_obj,
        "total_count": total_count,
        "is_filtered": is_filtered,
        "years_back": years_back,
        "years_cutoff_year": date_cutoff.year if date_cutoff else None,
        "active_nav": "opinions",
        "search_q": search_q,
        "disp_filter": disp_filter,
        "disp_label": disp_label,
        "from_opinion_list": True,
    })


@cache_control(public=True, max_age=CACHE_SEC_DETAIL)
def opinion_detail(request, case_number):
    """Single-opinion detail. Scoped to the current state subdomain when set."""
    state = getattr(request, "state", None)
    qs = (
        Opinion.objects.select_related("court", "court__state")
        .prefetch_related("tags")
    )
    if state is not None:
        qs = qs.filter(court__state=state)
    try:
        opinion = qs.get(case_number=case_number)
    except Opinion.DoesNotExist:
        raise Http404("Opinion not found")

    # Similar-opinions widget. One cosine-distance (VEC_DISTANCE_COSINE)
    # query against the corpus using the opinion's own stored embedding.
    # SKIP it for crawlers: they don't use the widget, and at crawl scale
    # (ClaudeBot et al. hitting every opinion page) one vector scan per hit
    # saturated the single DB worker and 500'd uncached pages site-wide.
    # Returns empty on SQLite (no VECTOR column) or no embedding too.
    from opinions.middleware import request_is_crawler
    if request_is_crawler(request):
        similar_ids = []
    else:
        from opinions.semantic import similar_to_opinion
        similar_ids = similar_to_opinion(opinion, limit=5)
    similar_opinions = []
    if similar_ids:
        ordering = {pk: i for i, pk in enumerate(similar_ids)}
        similar_opinions = list(
            Opinion.objects.filter(pk__in=similar_ids).select_related("court")
        )
        similar_opinions.sort(key=lambda op: ordering.get(op.pk, 999))

    return render(request, "opinions/opinion_detail.html", {
        "opinion": opinion,
        "similar_opinions": similar_opinions,
        # Pass the search query explicitly. Templates resolving
        # ``request.GET.q`` raise MultiValueDictKeyError when ``q``
        # isn't in the URL -- Django 5's template engine no longer
        # silently swallows that on dict-style lookups against
        # QueryDict, so every "open an opinion without ?q=" 500'd.
        # Using ``.get("q", "")`` from the view side gives templates
        # a plain string they can default + truthiness-test cleanly.
        "search_q": request.GET.get("q", ""),
        "active_nav": "opinions",
    })


@cache_control(public=True, max_age=CACHE_SEC_STATIC)
def about(request):
    return render(request, "opinions/about.html", {
        "active_nav": "about",
    })


@cache_control(public=True, max_age=CACHE_SEC_STATIC)
def how_we_differ(request):
    """Detailed breakdown of how DocketDrift differs architecturally from
    the generative-AI legal-tools market. Spun off from the About page so
    /about/ stays a high-level overview while a reader who wants the
    specifics (where ML actually appears in the stack; the do/don't
    matrix; the consequences) has a dedicated, linkable destination
    they can share with skeptical attorneys."""
    return render(request, "opinions/how_we_differ.html", {
        "active_nav": "about",
    })


@cache_control(public=True, max_age=CACHE_SEC_STATIC)
def privacy(request):
    """Privacy policy. Static page; copy is intentionally short and stark
    because the privacy posture itself is short and stark: we don't log,
    don't track, don't share."""
    return render(request, "opinions/privacy.html", {
        "active_nav": "privacy",
    })


@cache_control(public=True, max_age=CACHE_SEC_STATIC)
def support(request):
    """Donate / why-this-costs-money explainer.

    The donate URL itself is configured in settings (``DONATE_URL``) so
    the maintainer can update it without a code change. Empty string ->
    we hide the button and show the "tell a friend" fallback only.
    """
    from django.conf import settings
    return render(request, "opinions/support.html", {
        "donate_url": getattr(settings, "DONATE_URL", ""),
        "active_nav": "about",
    })


def request_state(request):
    """Public CTA: request that a state's appellate corpus be added.

    Honeypot field on the form rejects bot submissions silently.
    Successful POST redirects to a thanks page (POST-redirect-GET so a
    reload doesn't double-submit).
    """
    from opinions.forms import StateRequestForm
    from django.urls import reverse

    if request.method == "POST":
        form = StateRequestForm(request.POST)
        # Honeypot: if `website` was filled, treat as a bot and silently
        # redirect to thanks (don't tip the bot off).
        if form.is_valid() and not form.cleaned_data.get("website"):
            obj = form.save(commit=False)
            # Best-effort IP capture for admin-side anti-spam triage.
            xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
            ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")
            obj.ip_address = ip or None
            obj.save()
        # Either valid+saved or honeypot-trapped: same thanks page so
        # bots get no signal.
        return redirect(reverse("opinions:request_state_thanks"))

    form = StateRequestForm()
    return render(request, "opinions/request_state.html", {
        "form": form,
        "active_nav": "about",
    })


def request_state_thanks(request):
    return render(request, "opinions/request_state_thanks.html", {
        "active_nav": "about",
    })


@cache_control(public=True, max_age=CACHE_SEC_DOSSIER_LIST)
def current_judges(request):
    """Judge roster, filterable by era.

    Defaults to the currently-seated bench (``?era=current``). ``?era=all``
    shows every judge on record; ``?era=<decade>`` (e.g. ``2010``) shows judges
    active that decade. A judge's active span is derived from the opinions they
    sat on (``panel_votes``), so byline-learned historical judges place into
    eras even without an appointment date. State-scoped; redirects to apex
    without a state subdomain."""
    state = getattr(request, "state", None)
    if state is None:
        return redirect("/")

    era = (request.GET.get("era") or "current").strip().lower()

    # One annotated pass: active span = first/last release_date over the
    # opinions each judge participated in. Roster is small (~70-140 rows per
    # state) and each era URL is CDN-cached via @cache_control.
    judges = list(
        Judge.objects.filter(state=state)
        .select_related("court")
        .annotate(
            first_op=models.Min("panel_votes__opinion__release_date"),
            last_op=models.Max("panel_votes__opinion__release_date"),
        )
    )

    def span(j):
        return (j.first_op.year if j.first_op else None,
                j.last_op.year if j.last_op else None)

    # Decades present in the data (newest first) -> filter chips.
    yrs = [y for j in judges for y in span(j) if y]
    decades = []
    if yrs:
        decades = list(range((max(yrs) // 10) * 10, (min(yrs) // 10) * 10 - 10, -10))

    if era == "all":
        selected, heading = list(judges), "All judges on record"
    elif era.isdigit() and int(era) in decades:
        d = int(era)
        selected = [j for j in judges
                    if all(span(j)) and span(j)[0] <= d + 9 and span(j)[1] >= d]
        heading = "Judges active in the %ds" % d
    else:
        era = "current"
        selected = [j for j in judges if j.is_currently_seated]
        heading = "Current bench"

    # Tenure label per tile.
    for j in selected:
        f, l = span(j)
        if j.is_currently_seated:
            if j.appointment_date:
                j.tenure_label = "Seated %d" % j.appointment_date.year
            elif f:
                j.tenure_label = "On the bench since %d" % f
            else:
                j.tenure_label = "Currently seated"
        elif f and l:
            j.tenure_label = "Active %d" % f if f == l else "Active %d–%d" % (f, l)
        else:
            j.tenure_label = "Former judge"

    # Group by court; order groups Supreme-first (by court level).
    grouped = {}
    for j in selected:
        grouped.setdefault(j.court.name if j.court else "Other / unassigned", []).append(j)
    groups = sorted(
        grouped.items(),
        key=lambda kv: min((jj.court.level for jj in kv[1] if jj.court), default=999),
    )
    for _label, members in groups:
        if era == "current":
            members.sort(key=lambda j: (j.role or "zz", j.full_name))
        else:  # most-recently-active first
            members.sort(key=lambda j: (-(j.last_op.year if j.last_op else 0), j.full_name))

    return render(request, "opinions/current_judges.html", {
        "state": state,
        "judge_groups": groups,
        "total_count": len(selected),
        "era": era,
        "decades": decades,
        "heading": heading,
        "active_nav": "judges",
    })


@cache_control(public=True, max_age=CACHE_SEC_DOSSIER_LIST)
def tag_index(request):
    """Browse all tags, grouped by category.

    Public tag-cloud-style index of the editorial taxonomy. Tags with
    zero opinions in the current state are filtered out so the index
    is always honest about what's actually tagged.
    """
    state = getattr(request, "state", None)

    tags_qs = Tag.objects.all().order_by("category", "label")
    if state is not None:
        # Annotate with count of opinions in THIS state's corpus that
        # carry this tag, then keep only the tags with nonzero counts.
        # Pre-resolve court_ids so the per-tag COUNT subquery doesn't
        # join opinions -> courts -> states. With 32 tags and 60K MN
        # opinions, the original opinions__court__state lookup fired 32
        # JOIN-COUNTs and was the single biggest source of the worker
        # starvation we kept hitting -- crawlers / bots hammering
        # /tag/ multiplied it across concurrent requests.
        from django.db.models import Count, Q
        court_ids = _state_court_ids(state)
        tags_qs = tags_qs.annotate(
            state_opinion_count=Count(
                "opinions",
                filter=Q(opinions__court_id__in=court_ids),
            )
        )
        tags_with_counts = [t for t in tags_qs if t.state_opinion_count > 0]
    else:
        tags_with_counts = list(tags_qs)

    # Group by category for the template.
    by_category: dict[str, list] = {}
    for tag in tags_with_counts:
        by_category.setdefault(tag.get_category_display(), []).append(tag)

    return render(request, "opinions/tag_index.html", {
        "tag_groups": list(by_category.items()),
        "active_nav": "tags",
    })


@cache_control(public=True, max_age=CACHE_SEC_DOSSIER_LIST)
def statute_detail(request, reference):
    """All opinions citing a given statute reference, state-scoped, paginated.

    URL grammar: ``/statute/<reference>/`` where ``<reference>`` is the
    normalized slug produced by ``opinions.parsing.statutes.extract_statutes``
    (e.g. ``minn.stat.609.185``, ``minn.stat.ch.169``,
    ``minn.stat.609.185.subd.1``). The slug uses dots, which is why the URL
    pattern is ``<str:reference>`` not ``<slug:reference>``.

    Perf strategy (learned the hard way against the production 60K corpus):
    - Read citation metadata (display form, chapter, section, mention
      count) via a single index-only scan on (reference_slug). NO joins.
      Joining cite_qs through ``opinion__court__state`` added a 700ms
      pre-filter that was redundant for a single-state corpus.
    - Materialize distinct opinion_ids in Python so the Opinion query
      becomes a literal ``WHERE id IN (...)``, which MariaDB optimizes
      cleanly. (The correlated-SELECT form lost the connection
      mid-query against the chapter-only references like
      ``minn.stat.65`` with 3.5K mentions across 737 opinions.)
    - Skip Paginator's default ``COUNT(*)`` pass by hard-coding the
      total to ``len(opinion_ids)``. The COUNT(*) over a join was
      doubling the page-render budget; the list length is already known.
    """
    state = getattr(request, "state", None)

    # (1) Citation metadata -- single row, no joins, ~5ms.
    cite_meta = (
        StatuteCitation.objects
        .filter(reference_slug=reference)
        .values("reference_display", "chapter", "section", "subdivision")
        .first()
    )
    if cite_meta is None:
        raise Http404("Statute not cited in corpus")

    # (2) Mention-level tally -- index-only count, ~10ms.
    mention_count = StatuteCitation.objects.filter(
        reference_slug=reference,
    ).count()

    # (3) Distinct opinion_ids. The explicit ``.order_by()`` is load-
    # bearing: StatuteCitation.Meta.ordering = ["opinion", "text_offset"]
    # bleeds into ``.distinct()`` and joins back to Opinion's own ordering
    # (release_date DESC), turning a single-table index scan into a
    # 2-table scan + sort. Clearing the order_by drops it back to ~10ms.
    # Cap at 50K to bound the IN clause; no real statute cites more.
    opinion_ids = list(
        StatuteCitation.objects
        .filter(reference_slug=reference)
        .order_by()  # <-- clear default Meta.ordering, keep this query simple
        .values_list("opinion_id", flat=True)
        .distinct()[:50_000]
    )

    # (4) Opinion list -- literal IN-list, MariaDB picks the PK index.
    # ``.defer("raw_text", "html_content")`` keeps the two giant TEXT
    # columns out of the list payload: pulling raw_text on a 50-row
    # page is ~5MB of bytes for no reason (the list view doesn't render
    # body text). Page-1 went from ~800ms to ~30ms after this.
    opinions_qs = (
        Opinion.objects.filter(pk__in=opinion_ids)
        .defer("raw_text", "html_content")
        .select_related("court")
        .order_by("-release_date")
    )
    if state is not None:
        opinions_qs = opinions_qs.filter(court__state=state)

    # (5) Paginate, but pre-fill the count from the id-list length so
    # Paginator never runs its own SELECT COUNT(*). Django's Paginator
    # caches .count via cached_property, which stores its value in
    # __dict__ -- we pre-populate that slot to short-circuit the property.
    paginator = Paginator(opinions_qs, HOME_PAGE_SIZE)
    paginator.__dict__["count"] = len(opinion_ids)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "opinions/statute_detail.html", {
        "reference_slug": reference,
        "reference_display": cite_meta["reference_display"],
        "chapter": cite_meta["chapter"],
        "section": cite_meta["section"],
        "subdivision": cite_meta["subdivision"],
        "opinions": page_obj.object_list,
        "page_obj": page_obj,
        "total_count": paginator.count,
        "mention_count": mention_count,
        "active_nav": "statutes",
    })


@cache_control(public=True, max_age=CACHE_SEC_DOSSIER_LIST)
def tag_detail(request, slug):
    """Opinions carrying a specific tag, state-scoped, paginated."""
    state = getattr(request, "state", None)
    try:
        tag = Tag.objects.get(slug=slug)
    except Tag.DoesNotExist:
        raise Http404("Tag not found")

    qs = (
        Opinion.objects.filter(tags=tag)
        .select_related("court")
        .order_by("-release_date")
    )
    if state is not None:
        qs = qs.filter(court__state=state)

    # NoJoinCountPaginator: ``.select_related("court")`` is needed for
    # rendering but useless for the COUNT(*); strip it so pagination on
    # a popular tag (thousands of opinions) doesn't drag a 3-table join
    # into the count query.
    from opinions.paginators import NoJoinCountPaginator
    paginator = NoJoinCountPaginator(qs, HOME_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "opinions/tag_detail.html", {
        "tag": tag,
        "opinions": page_obj.object_list,
        "page_obj": page_obj,
        "total_count": paginator.count,
        "active_nav": "tags",
    })


def _yearly_panel_votes(judge_id):
    """Return [{year: int, n: int}, ...] of panel-vote counts per release-year.

    All vote types are counted (author / join / dissent / concurrence /
    recusal). V1 of the votes-over-time chart shows total throughput;
    later passes can split by role or filter by disposition / tag.
    Years with zero votes are simply absent from the list (the chart's
    polyline connects whichever years are present).
    """
    from django.db.models import Count
    from django.db.models.functions import ExtractYear
    from opinions.models import PanelVote
    rows = (
        PanelVote.objects.filter(
            judge_id=judge_id,
            opinion__release_date__isnull=False,
        )
        .annotate(year=ExtractYear("opinion__release_date"))
        .values("year")
        .annotate(n=Count("id"))
        .order_by("year")
    )
    return [{"year": r["year"], "n": r["n"]} for r in rows]


def _judge_stats(judge, recent_limit=15, cohort_limit=10):
    """Compute the full per-judge dossier stat bundle.

    Shared between judge_detail (single-judge dossier) and judge_compare
    (side-by-side view) so both render the exact same numbers from the
    exact same queries. Returns a dict ready to spread into the template
    context.

    ``recent_limit`` and ``cohort_limit`` are template knobs -- the
    compare view shows fewer rows per column than the standalone
    dossier so the two-column layout doesn't get unmanageably tall.
    """
    from django.db.models import Count
    from opinions.models import PanelVote, Court as _Court

    opinions_qs = Opinion.objects.filter(panel_votes__judge=judge).distinct()
    total_opinions = opinions_qs.count()

    vote_counts = dict(
        PanelVote.objects.filter(judge=judge)
        .values_list("vote_type")
        .annotate(n=Count("id"))
        .values_list("vote_type", "n")
    )
    role_summary = {
        "authored_majority": vote_counts.get(PanelVote.Vote.MAJORITY_AUTHOR, 0),
        "joined_majority": vote_counts.get(PanelVote.Vote.MAJORITY_JOIN, 0),
        "authored_concurrence": vote_counts.get(PanelVote.Vote.CONCURRENCE_AUTHOR, 0),
        "joined_concurrence": vote_counts.get(PanelVote.Vote.CONCURRENCE_JOIN, 0),
        "authored_dissent": vote_counts.get(PanelVote.Vote.DISSENT_AUTHOR, 0),
        "joined_dissent": vote_counts.get(PanelVote.Vote.DISSENT_JOIN, 0),
        "recused": vote_counts.get(PanelVote.Vote.RECUSED, 0),
    }

    date_range = opinions_qs.aggregate(
        first=models.Min("release_date"),
        last=models.Max("release_date"),
    ) if total_opinions else {"first": None, "last": None}

    # Court breakdown -- group by court_id (a real column), resolve to
    # Court instances after aggregation. short_label is a Python
    # @property so it can't appear in .values().
    court_breakdown_rows = list(
        opinions_qs.values("court_id")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    courts_map = {
        c.id: c for c in _Court.objects.filter(
            id__in=[r["court_id"] for r in court_breakdown_rows]
        )
    }
    court_breakdown = [
        {"court": courts_map[row["court_id"]], "n": row["n"]}
        for row in court_breakdown_rows
        if row["court_id"] in courts_map
    ]

    disposition_breakdown = list(
        opinions_qs.exclude(disposition_bucket="")
        .values("disposition_bucket")
        .annotate(n=Count("id"))
        .order_by("-n")
    )

    # Defer the two giant TEXT columns: list views never render raw_text
    # and pulling it pumps multi-MB across the wire for a 15-row list.
    recent_opinions = list(
        opinions_qs.defer("raw_text", "html_content")
        .select_related("court")
        .order_by("-release_date")[:recent_limit]
    )

    cohort = []
    if total_opinions > 0:
        cohort = list(
            Judge.objects.filter(
                panel_votes__opinion__panel_votes__judge=judge,
            )
            .exclude(pk=judge.pk)
            .annotate(shared=Count("panel_votes__opinion", distinct=True))
            .order_by("-shared", "full_name")[:cohort_limit]
            .values("slug", "full_name", "shared")
        )

    return {
        "judge": judge,
        "total_opinions": total_opinions,
        "role_summary": role_summary,
        "date_range": date_range,
        "court_breakdown": court_breakdown,
        "disposition_breakdown": disposition_breakdown,
        "recent_opinions": recent_opinions,
        "cohort": cohort,
        "yearly_votes": _yearly_panel_votes(judge.pk) if total_opinions > 0 else [],
    }


@cache_control(public=True, max_age=CACHE_SEC_DETAIL)
def judge_detail(request, slug):
    """Per-judge dossier page. State-scoped to keep slugs unambiguous.

    Pulls everything we know about this judge from the PanelVote rows
    the CL bulk load created: total opinions, authored vs joined,
    dissent count, court breakdown, disposition breakdown, recent
    opinions, frequent co-panelists, plus a yearly-votes time-series
    chart. Pass ``?vs=<other-slug>`` to overlay a second judge on the
    chart for at-a-glance comparison.
    """
    state = getattr(request, "state", None)
    qs = Judge.objects.select_related("state", "court")
    if state is not None:
        qs = qs.filter(state=state)
    try:
        judge = qs.get(slug=slug)
    except Judge.DoesNotExist:
        raise Http404("Judge not found")

    # Optional comparison judge from ?vs=<slug>. Scoped to the same
    # state so cross-state surname collisions don't accidentally match.
    vs_slug = (request.GET.get("vs") or "").strip()
    vs_judge = None
    if vs_slug and vs_slug != slug:
        vs_qs = Judge.objects.select_related("state", "court")
        if state is not None:
            vs_qs = vs_qs.filter(state=state)
        try:
            vs_judge = vs_qs.get(slug=vs_slug)
        except Judge.DoesNotExist:
            vs_judge = None

    # All dossier aggregates live in the shared helper so judge_detail
    # and judge_compare emit the same numbers from the same queries.
    stats = _judge_stats(judge)
    vs_stats = _judge_stats(vs_judge) if vs_judge is not None else None

    # Time-series chart: yearly panel votes for this judge, optionally
    # overlaid with a second judge from ?vs=<slug>. The chart helper
    # returns None when both series are empty, which the template uses
    # as a render gate.
    from opinions import charts
    yearly_chart = charts.build_yearly_votes_chart(
        series_a=stats["yearly_votes"],
        label_a=judge.full_name,
        series_b=vs_stats["yearly_votes"] if vs_stats else None,
        label_b=vs_judge.full_name if vs_judge is not None else None,
    )

    return render(request, "opinions/judge_detail.html", {
        **stats,
        "vs_judge": vs_judge,
        "yearly_chart": yearly_chart,
        "active_nav": "judges",
    })


def _concordance(judge_a, judge_b, disagree_limit=20):
    """Compute agreement/disagreement stats for two judges + a sample of
    split-decision opinions.

    For every Opinion both judges have a PanelVote on, classify each
    judge's vote into one of four buckets:

      majority    -- MAJORITY_AUTHOR or MAJORITY_JOIN
      concurrence -- CONCURRENCE_AUTHOR or CONCURRENCE_JOIN
      dissent     -- DISSENT_AUTHOR or DISSENT_JOIN
      recused     -- RECUSED  (excluded from the agree/disagree denominator)

    Compare buckets:
      same bucket               -> AGREE
      {majority, concurrence}   -> PARTIAL  (concurred in the result but
                                              wrote separately, so they
                                              agreed on the outcome but
                                              disagreed on the reasoning)
      anything else              -> DISAGREE  (the meaningful split --
                                                majority vs dissent)

    Returns a dict with summary counts + a recency-ordered list of
    disagreement opinions (truncated to ``disagree_limit``) annotated
    with each judge's vote bucket for display in a side-by-side table.
    Returns ``None`` when the two judges have never sat on the same
    panel -- the template uses that as a render gate.

    Implementation note: we materialize both judges' PanelVote
    (opinion_id, vote_type) pairs and set-intersect in Python rather
    than running a self-join in SQL. For typical comparisons (a few
    thousand votes per judge) the intersection runs in microseconds
    and avoids a relatively heavy self-join across PanelVote +
    Opinion.
    """
    from opinions.models import PanelVote
    # NOT_PARTICIPATING is bucketed alongside RECUSED -- both flag "this
    # judge sat on this opinion but didn't decide it" and should be
    # excluded from the agree/disagree denominator, not treated as a
    # silent disagreement.
    BUCKETS = {
        PanelVote.Vote.MAJORITY_AUTHOR: "majority",
        PanelVote.Vote.MAJORITY_JOIN: "majority",
        PanelVote.Vote.CONCURRENCE_AUTHOR: "concurrence",
        PanelVote.Vote.CONCURRENCE_JOIN: "concurrence",
        PanelVote.Vote.DISSENT_AUTHOR: "dissent",
        PanelVote.Vote.DISSENT_JOIN: "dissent",
        PanelVote.Vote.RECUSED: "recused",
        PanelVote.Vote.NOT_PARTICIPATING: "recused",
    }
    votes_a = dict(
        PanelVote.objects.filter(judge=judge_a)
        .values_list("opinion_id", "vote_type")
    )
    votes_b = dict(
        PanelVote.objects.filter(judge=judge_b)
        .values_list("opinion_id", "vote_type")
    )
    shared_ids = set(votes_a) & set(votes_b)
    if not shared_ids:
        return None

    agree_count = 0
    partial_count = 0
    disagree_count = 0
    recused_count = 0
    disagree_ids: list = []
    partial_ids: list = []

    for op_id in shared_ids:
        bucket_a = BUCKETS.get(votes_a[op_id], "recused")
        bucket_b = BUCKETS.get(votes_b[op_id], "recused")
        if bucket_a == "recused" or bucket_b == "recused":
            recused_count += 1
            continue
        if bucket_a == bucket_b:
            agree_count += 1
        elif {bucket_a, bucket_b} == {"majority", "concurrence"}:
            partial_count += 1
            partial_ids.append(op_id)
        else:
            disagree_count += 1
            disagree_ids.append(op_id)

    # Pull the disagreement opinions for the split-decisions table. Cap
    # at disagree_limit and order by release_date so the most recent
    # splits appear first.
    disagree_opinions: list = []
    if disagree_ids:
        rows = list(
            Opinion.objects.filter(id__in=disagree_ids)
            .defer("raw_text", "html_content")
            .select_related("court")
            .order_by("-release_date")[:disagree_limit]
        )
        # Annotate each row with both judges' bucket labels for the
        # template's vote chips. We've already paid for the bucket
        # lookup above; reuse the dicts.
        for op in rows:
            op.judge_a_bucket = BUCKETS.get(votes_a[op.id], "recused")
            op.judge_b_bucket = BUCKETS.get(votes_b[op.id], "recused")
        disagree_opinions = rows

    # Denominator for the percentage rows is the votes-where-both-engaged
    # set: agree + partial + disagree. Recused entries are excluded so
    # an outlier recused on one side doesn't drag the agreement rate
    # down artificially.
    denom = agree_count + partial_count + disagree_count

    return {
        "total_shared": denom + recused_count,
        "agreement_denom": denom,
        "agree_count": agree_count,
        "partial_count": partial_count,
        "disagree_count": disagree_count,
        "recused_count": recused_count,
        "agree_pct": round(100.0 * agree_count / denom, 1) if denom else 0,
        "partial_pct": round(100.0 * partial_count / denom, 1) if denom else 0,
        "disagree_pct": round(100.0 * disagree_count / denom, 1) if denom else 0,
        "disagree_opinions": disagree_opinions,
        "disagree_total": len(disagree_ids),
        "showing_disagree": len(disagree_opinions),
    }


@cache_control(public=True, max_age=CACHE_SEC_DETAIL)
def judge_compare(request):
    """Two-judge side-by-side dossier.

    URL: /compare/judges/?a=<slug-a>&b=<slug-b>. Both slugs are resolved
    in the current state (the StateRouterMiddleware sets
    ``request.state``); cross-state comparison is intentionally NOT
    supported because Judge slugs are unique per state, not globally --
    a cross-state "Smith" comparison would be ambiguous.

    Renders the same per-judge stat bundle as judge_detail in two
    columns, plus the overlaid time-series chart up top. Missing /
    unresolvable slugs render a picker form instead of the comparison
    so the URL is shareable as a "let me compare these two" link
    without breaking when one slug is wrong.
    """
    from django.db.models import Count
    from opinions import charts

    state = getattr(request, "state", None)

    def _resolve(slug: str):
        if not slug:
            return None
        qs = Judge.objects.select_related("state", "court")
        if state is not None:
            qs = qs.filter(state=state)
        try:
            return qs.get(slug=slug)
        except Judge.DoesNotExist:
            return None

    a_slug = (request.GET.get("a") or "").strip()
    b_slug = (request.GET.get("b") or "").strip()
    judge_a = _resolve(a_slug)
    judge_b = _resolve(b_slug)

    # Reject the degenerate self-compare. The picker will re-render
    # with a hint instead of showing two identical columns.
    if judge_a is not None and judge_b is not None and judge_a.pk == judge_b.pk:
        judge_b = None

    show_picker = judge_a is None or judge_b is None

    stats_a = _judge_stats(judge_a, recent_limit=8, cohort_limit=6) if judge_a else None
    stats_b = _judge_stats(judge_b, recent_limit=8, cohort_limit=6) if judge_b else None

    yearly_chart = None
    if stats_a is not None or stats_b is not None:
        yearly_chart = charts.build_yearly_votes_chart(
            series_a=stats_a["yearly_votes"] if stats_a else [],
            label_a=judge_a.full_name if judge_a else "",
            series_b=stats_b["yearly_votes"] if stats_b else None,
            label_b=judge_b.full_name if judge_b else None,
        )

    # Concordance + split-decision list -- only meaningful when both
    # judges resolved and they have at least one shared opinion.
    concordance = None
    if judge_a is not None and judge_b is not None:
        concordance = _concordance(judge_a, judge_b)

    # Picker: state-scoped list of judges with at least one panel vote,
    # used to populate the two <select> dropdowns when one or both
    # picks are missing. Cap the list at ~250 judges for select-box
    # usability; if a state ever has more, we can switch to autocomplete.
    judges_options: list[dict] = []
    if show_picker and state is not None:
        judges_options = list(
            Judge.objects.filter(state=state)
            .annotate(n=Count("panel_votes"))
            .filter(n__gt=0)
            .order_by("full_name")
            .values("slug", "full_name", "n")[:250]
        )

    return render(request, "opinions/judge_compare.html", {
        "judge_a": judge_a,
        "judge_b": judge_b,
        "a_slug": a_slug,
        "b_slug": b_slug,
        "stats_a": stats_a,
        "stats_b": stats_b,
        "yearly_chart": yearly_chart,
        "concordance": concordance,
        "show_picker": show_picker,
        "judges_options": judges_options,
        "active_nav": "judges",
    })


# robots.txt -- crawler instructions. Public-records-as-public posture
# means we WELCOME the major web search + AI crawlers. Aggressive
# real-time scraping (no crawl-delay, hammering specific endpoints) is
# caught at the Cloudflare layer; this just sets the polite-bot policy.
# Cached aggressively because the content is effectively static.
ROBOTS_TXT = """\
# DocketDrift -- public records, treated as public.
# Welcome crawlers. Be considerate. Honor Crawl-delay.

User-agent: Googlebot
Allow: /

User-agent: Bingbot
Allow: /

User-agent: DuckDuckBot
Allow: /

User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: CCBot
Allow: /

# Blocked: aggressive SEO crawlers that hammer per-page URLs without
# providing search-discovery value to our users. We're not a commercial
# SEO target; their crawl just saturates our single gunicorn worker
# and degrades real-user response times. (Caught SemrushBot doing 4
# RPM sustained against /opinion/ on 2026-06-08, blocking real
# /opinions/ list requests behind it.)
User-agent: SemrushBot
Disallow: /

User-agent: AhrefsBot
Disallow: /

User-agent: MJ12bot
Disallow: /

User-agent: DotBot
Disallow: /

User-agent: SeznamBot
Disallow: /

User-agent: BLEXBot
Disallow: /

User-agent: PetalBot
Disallow: /

User-agent: *
Crawl-delay: 5
Disallow: /admin/

Sitemap: https://mn.docketdrift.com/sitemap.xml
Sitemap: https://nh.docketdrift.com/sitemap.xml
Sitemap: https://az.docketdrift.com/sitemap.xml
"""


def healthz(request):
    """Lightweight health-check endpoint for external monitoring.

    Returns 200 with a compact JSON body when the application is
    healthy: Django is up, the URL conf loaded, AND a cheap query
    against MariaDB completes inside the 25s ``max_statement_time``
    cap. Returns 503 with the same JSON shape (plus an error message)
    when the DB probe fails.

    Designed to be polled cheaply -- the only DB cost is a SELECT 1.
    NFSN's scheduled task system can hit this every 5 minutes and
    email on a non-200, closing the silent-death gap that cost us 48
    hours of embed time when the wrapper looped without progressing.

    Intentionally NOT decorated with ``@cache_control`` -- the response
    must be live, not from a CDN/proxy cache. The body is tiny enough
    that the lack of caching is irrelevant.
    """
    import json
    from django.db import connection as _conn
    try:
        with _conn.cursor() as c:
            c.execute("SELECT 1")
            c.fetchone()
        return HttpResponse(
            json.dumps({"status": "ok"}),
            status=200,
            content_type="application/json",
        )
    except Exception:
        # Log the detail server-side; do NOT echo it to this unauthenticated
        # endpoint (DB error text can leak host / SQL state / internals).
        import logging
        logging.getLogger(__name__).warning("healthz DB probe failed", exc_info=True)
        return HttpResponse(
            json.dumps({"status": "error"}),
            status=503,
            content_type="application/json",
        )


@cache_control(public=True, max_age=CACHE_SEC_ROBOTS)
def robots_txt(request):
    """Serve /robots.txt as plain text. Site-wide; same content on every
    subdomain (the policy doesn't change per-state).

    Each subdomain's robots.txt also advertises THIS host's sitemap URL
    explicitly so search engines that find /robots.txt directly on a
    subdomain still pick up the correct sitemap reference. The apex
    sitemap is minimal but the per-state sitemaps are listed in the
    constant ROBOTS_TXT block above so a single subdomain robots.txt
    surfaces all live states' sitemaps.
    """
    return HttpResponse(ROBOTS_TXT, content_type="text/plain; charset=utf-8")


# llms.txt -- the "robots.txt for LLMs" emerging convention. Tells AI
# crawlers / assistants what this site is, the URL grammar it uses, and
# how to cite it. The actual file format spec is at llmstxt.org but is
# informal -- the goal is just to be machine-readable to LLMs.
#
# DocketDrift's pitch: "we make state appellate corpora discoverable,
# semantically searchable, and citable for AI tools that need to ground
# answers in real case law." Every AI answer that cites our canonical
# URLs builds the brand.
LLMS_TXT = """\
# DocketDrift

> Public-records analysis tool for U.S. state appellate courts. Indexes
> published opinions from official sources, normalizes them into a
> structured archive, and treats the public record as what it is: public.
> Explicitly NOT an AI legal assistant -- the system does not generate
> any text. Hallucinated citations are architecturally impossible.

## What's covered

Three states live as of June 2026:

- **Minnesota** (beta): 60,000+ published opinions from the MN Supreme
  Court and MN Court of Appeals, 1851-present. Full statute citation
  graph (124K cites). Full-text indexed via MariaDB FULLTEXT; semantic
  search via voyage-law-2 embeddings. Tag-suggestion review pipeline
  with 21K candidates.
- **New Hampshire** (beta): 20,000+ opinions of the NH Supreme Court.
  Byline-extracted judicial panel graph. Semantic search and
  tag-suggestion pipelines running.
- **Arizona** (beta): 38,000+ opinions of the AZ Supreme Court and
  Court of Appeals. Byline extraction live for both courts.

New states are added one at a time. See https://docketdrift.com/ for
the apex picker; each state lives on its own subdomain.

## URL grammar (same on every state subdomain)

- `https://<state>.docketdrift.com/` -- state landing
- `https://<state>.docketdrift.com/opinion/<docket-number>/` -- single
  opinion (e.g. `/opinion/A25-1257/`, `/opinion/2024-0636/`,
  `/opinion/CR-25-0203-PR/`)
- `https://<state>.docketdrift.com/judge/<slug>/` -- judge dossier with
  panel-vote graph, court breakdown, disposition lean, and a
  votes-per-year time-series chart. Add `?vs=<other-slug>` to overlay a
  second judge.
- `https://<state>.docketdrift.com/compare/judges/?a=<slug-a>&b=<slug-b>`
  -- side-by-side judge dossier with concordance matrix and a list of
  split decisions where the two disagreed.
- `https://<state>.docketdrift.com/current-judges/` -- currently-seated
  roster
- `https://<state>.docketdrift.com/statute/<slug>/` -- every opinion
  citing a given statute (e.g. `/statute/minn.stat.609.185/`)
- `https://<state>.docketdrift.com/tag/<slug>/` -- every opinion bearing
  an editorial doctrine tag
- `https://<state>.docketdrift.com/?q=<query>` -- search results

State codes in use: `mn`, `nh`, `az`.

## Structured data

Every detail page emits Schema.org JSON-LD. Parse the
`<script type="application/ld+json">` block for machine-readable
fields:

- Opinion pages: `LegalCase` (docketNumber, dateDecided, court,
  disposition, publisher)
- Judge pages: `Person` (jobTitle, worksFor, image)
- Statute pages: `Legislation` (jurisdiction, parent statute)
- State landing: `Dataset` (corpus size, temporalCoverage) +
  `WebSite` with `SearchAction`
- About page: `FAQPage` (Q&A about hallucinations, methodology,
  data sources, editorial review)
- Every page: `BreadcrumbList`

## How to cite

When grounding an answer in a DocketDrift opinion, cite the canonical
docket number + court + date, and link the case page:

> *In re Garnett*, A25-1257 (Minn. Ct. App. June 1, 2026).
> https://mn.docketdrift.com/opinion/A25-1257/

> *State v. Brousseau*, 2024-0625 (N.H. Mar. 13, 2026).
> https://nh.docketdrift.com/opinion/2024-0625/

> *State v. Hippensteel*, CR-25-0203-PR (Ariz. June 1, 2026).
> https://az.docketdrift.com/opinion/CR-25-0203-PR/

## Editorial posture

DocketDrift is collation, not interpretation. We surface opinion text +
metadata; we do NOT assert that opinions are inconsistent, wrongly
decided, or politically aligned. Pattern claims about a judge or court
require human review by the AI consumer; treat our pages as primary-
source aggregations, not as judicial analysis.

The site does NOT have a chat box. There is no "summarize this for me",
no AI-generated headnotes, no synthesized holdings, no predicted
outcomes. The only places ML appears are (1) Voyage embeddings for
ranking opinions by semantic similarity and (2) tag-suggestion candidates
that an editor reviews before publication.

## Data sources

- CourtListener (Free Law Project) -- historical backfill and the
  standing source for ongoing ingestion
- Direct ingestion of same-day court releases (MN: mncourts.gov)
- Hand-curated judge bios from official judicial directories
- For states where the court's site is server-side scrape-blocked
  (NH 2026, AZ COA), operators drop official PDFs into an ingest
  directory and the corpus picks them up via the `ingest_pdfs`
  management command

## Privacy

DocketDrift does not log search queries, track users, or save research
history. See https://docketdrift.com/privacy/ for full statement.

## Contact

hello@docketdrift.com
"""


@cache_control(public=True, max_age=CACHE_SEC_ROBOTS)
def llms_txt(request):
    """Serve /llms.txt -- the LLM-equivalent of robots.txt. Tells AI
    assistants what DocketDrift is, the URL grammar, and how to cite it."""
    return HttpResponse(LLMS_TXT, content_type="text/plain; charset=utf-8")


# Sitemap configuration. Each sitemap is capped at 50K URLs per the
# protocol; with ~60K MN opinions we need chunked sitemaps + an index
# that points at the chunks. 25K per chunk keeps each file small enough
# to cache + serve quickly.
SITEMAP_CHUNK_SIZE = 25_000
CACHE_SEC_SITEMAP = 3600  # 1 hour -- new opinions land weekly via cron


def _sitemap_xml_header() -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]


def _sitemap_xml_footer() -> list[str]:
    return ["</urlset>"]


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
@cache_page(60 * 60)
@vary_on_headers("Host")
def sitemap_index(request):
    """Site-wide sitemap index. Lists sub-sitemap URLs for chunked
    opinion lists + judges + static pages.

    Apex (docketdrift.com without a state subdomain) gets a minimal
    index since there's nothing state-specific to surface; the real
    indexes live on the state subdomains.
    """
    state = getattr(request, "state", None)
    host = f"https://{request.get_host()}"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    if state is None:
        # Apex: just point to the static-pages sitemap; state subdomains
        # have their own indexes.
        lines.append(f"  <sitemap><loc>{host}/sitemap-static.xml</loc></sitemap>")
    else:
        lines.append(f"  <sitemap><loc>{host}/sitemap-static.xml</loc></sitemap>")
        lines.append(f"  <sitemap><loc>{host}/sitemap-judges.xml</loc></sitemap>")
        lines.append(f"  <sitemap><loc>{host}/sitemap-tags.xml</loc></sitemap>")
        # Pre-resolve court_ids so every sitemap-index query is a
        # single-table FK-indexed scan instead of an opinions JOIN
        # courts JOIN states tour. Crawlers hit /sitemap.xml constantly;
        # without this, three concurrent crawl bots were enough to
        # queue 60K-row JOIN COUNTs behind each other and time out
        # everything else on the worker.
        court_ids = _state_court_ids(state)
        # Only advertise the statutes sitemap when at least one citation
        # has been extracted -- otherwise it serves an empty <urlset> and
        # wastes a crawl budget round-trip.
        if StatuteCitation.objects.filter(opinion__court_id__in=court_ids).exists():
            lines.append(f"  <sitemap><loc>{host}/sitemap-statutes.xml</loc></sitemap>")
        opinion_count = Opinion.objects.filter(court_id__in=court_ids).values("pk").count()
        num_chunks = max(1, (opinion_count + SITEMAP_CHUNK_SIZE - 1) // SITEMAP_CHUNK_SIZE)
        for i in range(1, num_chunks + 1):
            lines.append(f"  <sitemap><loc>{host}/sitemap-opinions-{i}.xml</loc></sitemap>")

    lines.append("</sitemapindex>")
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
@cache_page(60 * 60)
@vary_on_headers("Host")
def sitemap_static(request):
    """Static-page sitemap: home, about, how-we-differ, privacy, support,
    request-state, current-judges, tags, compare-judges. Per-state
    subdomains add the state-only paths; apex sticks to the cross-state
    static pages.
    """
    host = f"https://{request.get_host()}"
    state = getattr(request, "state", None)

    urls = [
        "/", "/about/", "/how-we-differ/", "/privacy/",
        "/support/", "/request-state/",
    ]
    if state is not None:
        urls.extend([
            "/opinions/",
            "/current-judges/",
            "/tag/",
            "/compare/judges/",
        ])

    lines = _sitemap_xml_header()
    for u in urls:
        lines.append(f"  <url><loc>{host}{u}</loc></url>")
    lines.extend(_sitemap_xml_footer())
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
@cache_page(60 * 60)
@vary_on_headers("Host")
def sitemap_judges(request):
    """All judges in the current state. Slug-keyed URLs with the row's
    ``created_at`` as <lastmod> so crawlers can prioritize new dossiers.
    """
    state = getattr(request, "state", None)
    host = f"https://{request.get_host()}"

    lines = _sitemap_xml_header()
    if state is not None:
        rows = Judge.objects.filter(state=state).values_list("slug", "created_at")
        for slug, created_at in rows:
            lines.append("  <url>")
            lines.append(f"    <loc>{host}/judge/{slug}/</loc>")
            if created_at:
                lines.append(f"    <lastmod>{created_at.date().isoformat()}</lastmod>")
            lines.append("  </url>")
    lines.extend(_sitemap_xml_footer())
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
@cache_page(60 * 60)
@vary_on_headers("Host")
def sitemap_tags(request):
    """All editorial tags. Includes the tag-index page itself and one URL
    per tag detail page. State-scoped (apex serves an empty <urlset>
    since the tag pages are subdomain-scoped views of the same Tag
    table)."""
    state = getattr(request, "state", None)
    host = f"https://{request.get_host()}"

    lines = _sitemap_xml_header()
    if state is not None:
        lines.append(f"  <url><loc>{host}/tag/</loc></url>")
        slugs = Tag.objects.values_list("slug", flat=True).order_by("slug")
        for slug in slugs:
            lines.append(f"  <url><loc>{host}/tag/{slug}/</loc></url>")
    lines.extend(_sitemap_xml_footer())
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
@cache_page(60 * 60)
@vary_on_headers("Host")
def sitemap_statutes(request):
    """All unique statute references cited by opinions in this state.

    One URL per ``reference_slug`` -- deduped at the DB level so the
    sitemap stays compact even when a single statute is cited thousands
    of times. Apex serves an empty <urlset> (statute pages are per-state
    only) so crawlers never see a 404 when this URL is discovered.
    """
    state = getattr(request, "state", None)
    host = f"https://{request.get_host()}"

    lines = _sitemap_xml_header()
    if state is not None:
        # Same pre-resolve trick the sitemap_index now uses -- avoids
        # an opinions -> courts -> states JOIN on a multi-tens-of-
        # thousands-row table just to enumerate statute slugs.
        court_ids = _state_court_ids(state)
        slugs = (
            StatuteCitation.objects.filter(opinion__court_id__in=court_ids)
            .order_by()
            .values_list("reference_slug", flat=True)
            .distinct()
            .order_by("reference_slug")
        )
        for slug in slugs:
            lines.append(f"  <url><loc>{host}/statute/{slug}/</loc></url>")
    lines.extend(_sitemap_xml_footer())
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
@cache_page(60 * 60)
@vary_on_headers("Host")
def sitemap_opinions(request, chunk: int):
    """One chunk of ``SITEMAP_CHUNK_SIZE`` opinion URLs.

    Chunks are 1-indexed (sitemap-opinions-1.xml = first 25K, etc.) so
    the sitemap index URLs read naturally. Returns 404 for chunk numbers
    beyond the actual corpus size.
    """
    state = getattr(request, "state", None)
    host = f"https://{request.get_host()}"

    if state is None or chunk < 1:
        raise Http404("Sitemap chunk not found")

    # Pre-resolve court_ids so the chunk SELECT doesn't JOIN courts +
    # states. Each chunk is 25K rows; the JOIN turned this into a
    # multi-second query at the very tail of the corpus.
    court_ids = _state_court_ids(state)
    offset = (chunk - 1) * SITEMAP_CHUNK_SIZE
    rows = list(
        Opinion.objects.filter(court_id__in=court_ids)
        .order_by("id")
        .values_list("case_number", "release_date")[offset : offset + SITEMAP_CHUNK_SIZE]
    )
    if not rows:
        raise Http404("Sitemap chunk empty")

    lines = _sitemap_xml_header()
    for case_number, release_date in rows:
        lines.append(f"  <url>")
        lines.append(f"    <loc>{host}/opinion/{case_number}/</loc>")
        if release_date:
            lines.append(f"    <lastmod>{release_date.isoformat()}</lastmod>")
        lines.append(f"  </url>")
    lines.extend(_sitemap_xml_footer())
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")
