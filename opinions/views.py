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
from django.core.paginator import Paginator
from django.db import connection, models
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import cache_control

from opinions.models import Judge, Opinion, State, Tag


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
            s.opinion_count = Opinion.objects.filter(court__state=s).count()
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
    state_opinions = Opinion.objects.filter(court__state=state)
    total_opinions = state_opinions.count()
    judges_qs = Judge.objects.filter(state=state)
    total_judges = judges_qs.count()
    currently_seated = judges_qs.filter(is_currently_seated=True).count()

    date_range = state_opinions.aggregate(
        first=models.Min("release_date"),
        last=models.Max("release_date"),
    )

    latest_opinions = list(
        state_opinions.select_related("court").order_by("-release_date")[:5]
    )

    total_tags_used = Tag.objects.filter(
        opinions__court__state=state,
    ).distinct().count()
    total_tags_available = Tag.objects.count()

    return render(request, "opinions/state_landing.html", {
        "state": state,
        "total_opinions": total_opinions,
        "total_judges": total_judges,
        "currently_seated": currently_seated,
        "latest_opinions": latest_opinions,
        "date_range": date_range,
        "total_tags_used": total_tags_used,
        "total_tags_available": total_tags_available,
        "active_nav": "home",
    })


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

    qs = (
        Opinion.objects.filter(court__state=state)
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
        use_fulltext = (
            connection.vendor == "mysql"
            and len(search_q) >= FULLTEXT_MIN_QUERY_LEN
        )
        if use_fulltext:
            # MATCH AGAINST against the FULLTEXT index for the big raw_text
            # field. Phrase-quoted in BOOLEAN MODE so multi-word queries
            # are treated as exact phrases (matches user intent better than
            # OR-of-tokens for legal text). LIKE still handles the short
            # case_number field since that's its own indexed unique pattern.
            qs = qs.extra(
                where=[
                    "(opinions_opinion.case_number LIKE %s OR "
                    "MATCH(opinions_opinion.raw_text, opinions_opinion.title) "
                    "AGAINST (%s IN BOOLEAN MODE))"
                ],
                params=[f"%{search_q}%", f'"{search_q}"'],
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
        paginator = Paginator(qs, HOME_PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page", 1))
        opinions = page_obj.object_list
        total_count = paginator.count
    else:
        # Default landing: just the most recent N. No paginator object so
        # the template knows we're in landing mode.
        page_obj = None
        opinions = list(qs[:HOME_LANDING_SIZE])
        total_count = qs.count()

    # Semantic search: when the user has typed a query, also run a
    # vector-similarity search alongside the keyword/FULLTEXT one and
    # surface a separate "semantically similar" block in the template.
    # Cached per-query, so repeat searches cost nothing. Skips silently
    # on local SQLite (no VECTOR column) and when Voyage isn't configured.
    semantic_opinions = []
    if search_q and not disp_filter:
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

    # Similar-opinions widget. Uses the opinion's own stored embedding
    # (no Voyage call), so this is effectively free at request time --
    # one cosine-distance query against the corpus. Returns empty list
    # on SQLite (no VECTOR column) or when the opinion has no embedding.
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
        "active_nav": "opinions",
    })


@cache_control(public=True, max_age=CACHE_SEC_STATIC)
def about(request):
    return render(request, "opinions/about.html", {
        "active_nav": "about",
    })


@cache_control(public=True, max_age=CACHE_SEC_STATIC)
def privacy(request):
    """Privacy policy. Static page; copy is intentionally short and stark
    because the privacy posture itself is short and stark: we don't log,
    don't track, don't share."""
    return render(request, "opinions/privacy.html", {
        "active_nav": "about",
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
    """Roster of currently-seated judges. State-scoped: redirects to apex
    when accessed without a state subdomain (no roster to show without one)."""
    state = getattr(request, "state", None)
    if state is None:
        return redirect("/")

    judges = (
        Judge.objects.filter(state=state, is_currently_seated=True)
        .select_related("court")
        .order_by("court__level", "role", "full_name")
    )
    # Group by court for display.
    grouped = {}
    for j in judges:
        court_label = j.court.name if j.court else "Unassigned"
        grouped.setdefault(court_label, []).append(j)
    groups = list(grouped.items())
    return render(request, "opinions/current_judges.html", {
        "state": state,
        "judge_groups": groups,
        "total_count": judges.count(),
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
        from django.db.models import Count, Q
        tags_qs = tags_qs.annotate(
            state_opinion_count=Count(
                "opinions",
                filter=Q(opinions__court__state=state),
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

    paginator = Paginator(qs, HOME_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "opinions/tag_detail.html", {
        "tag": tag,
        "opinions": page_obj.object_list,
        "page_obj": page_obj,
        "total_count": paginator.count,
        "active_nav": "tags",
    })


@cache_control(public=True, max_age=CACHE_SEC_DETAIL)
def judge_detail(request, slug):
    """Per-judge dossier page. State-scoped to keep slugs unambiguous.

    Pulls everything we know about this judge from the PanelVote rows
    the CL bulk load created: total opinions, authored vs joined,
    dissent count, court breakdown, disposition breakdown, recent
    opinions, frequent co-panelists. Heavy query (~5-10 aggregates +
    a recent-list query) but cached for an hour so subsequent requests
    are free.
    """
    state = getattr(request, "state", None)
    qs = Judge.objects.select_related("state", "court")
    if state is not None:
        qs = qs.filter(state=state)
    try:
        judge = qs.get(slug=slug)
    except Judge.DoesNotExist:
        raise Http404("Judge not found")

    from django.db.models import Count, Q
    from opinions.models import PanelVote

    # Total opinions the judge sat on
    opinions_qs = Opinion.objects.filter(panel_votes__judge=judge).distinct()
    total_opinions = opinions_qs.count()

    # Vote-type breakdown (authored vs joined vs dissent etc)
    vote_counts = dict(
        PanelVote.objects.filter(judge=judge)
        .values_list("vote_type")
        .annotate(n=Count("id"))
        .values_list("vote_type", "n")
    )

    # Group authored / joined / dissent at a higher level so display
    # is "Majority opinions authored: N, Joined majority: N, Dissents: N"
    role_summary = {
        "authored_majority": vote_counts.get(PanelVote.Vote.MAJORITY_AUTHOR, 0),
        "joined_majority": vote_counts.get(PanelVote.Vote.MAJORITY_JOIN, 0),
        "authored_concurrence": vote_counts.get(PanelVote.Vote.CONCURRENCE_AUTHOR, 0),
        "joined_concurrence": vote_counts.get(PanelVote.Vote.CONCURRENCE_JOIN, 0),
        "authored_dissent": vote_counts.get(PanelVote.Vote.DISSENT_AUTHOR, 0),
        "joined_dissent": vote_counts.get(PanelVote.Vote.DISSENT_JOIN, 0),
        "recused": vote_counts.get(PanelVote.Vote.RECUSED, 0),
    }

    # Date range -- when did this judge sit on opinions we have?
    date_range = opinions_qs.aggregate(
        first=models.Min("release_date"),
        last=models.Max("release_date"),
    ) if total_opinions else {"first": None, "last": None}

    # Court breakdown -- Supreme vs Court of Appeals splits via panel
    # votes, since a single judge can sit on multiple courts over time.
    court_breakdown = list(
        opinions_qs.values(
            "court__short_label",
            "court__level",
        )
        .annotate(n=Count("id"))
        .order_by("-n")
    )

    # Disposition breakdown for the disposition pill colors.
    disposition_breakdown = list(
        opinions_qs.exclude(disposition_bucket="")
        .values("disposition_bucket")
        .annotate(n=Count("id"))
        .order_by("-n")
    )

    # Recent opinions list -- 15 most recent we have a panel-vote for
    recent_opinions = list(
        opinions_qs.select_related("court")
        .order_by("-release_date")[:15]
    )

    # Frequent co-panelists -- other judges who appeared on the same
    # opinions, ranked by overlap count. Capped at top 10. Heavy query;
    # only computed when the judge has > 0 opinions.
    cohort = []
    if total_opinions > 0:
        cohort = list(
            Judge.objects.filter(
                panel_votes__opinion__panel_votes__judge=judge,
            )
            .exclude(pk=judge.pk)
            .annotate(shared=Count("panel_votes__opinion", distinct=True))
            .order_by("-shared", "full_name")[:10]
            .values("slug", "full_name", "shared")
        )

    return render(request, "opinions/judge_detail.html", {
        "judge": judge,
        "total_opinions": total_opinions,
        "role_summary": role_summary,
        "date_range": date_range,
        "court_breakdown": court_breakdown,
        "disposition_breakdown": disposition_breakdown,
        "recent_opinions": recent_opinions,
        "cohort": cohort,
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

User-agent: *
Crawl-delay: 5
Disallow: /admin/

Sitemap: https://mn.docketdrift.com/sitemap.xml
"""


@cache_control(public=True, max_age=CACHE_SEC_ROBOTS)
def robots_txt(request):
    """Serve /robots.txt as plain text. Site-wide; same content on every
    subdomain (the policy doesn't change per-state)."""
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

## What's covered

- **Minnesota** (beta): 60,000+ published opinions from the MN Supreme
  Court and MN Court of Appeals, 1930-present. Full text indexed via
  MariaDB FULLTEXT; semantic search via voyage-law-2 embeddings.
- Other states: planned, expanding state-by-state as funding allows.

## URL grammar (Minnesota)

- `https://mn.docketdrift.com/` -- state landing, 10 most recent opinions
- `https://mn.docketdrift.com/opinion/<docket-number>/` -- single opinion
  (e.g. `/opinion/A25-1257/`)
- `https://mn.docketdrift.com/judge/<slug>/` -- judge dossier
  (e.g. `/judge/natalie-e-hudson/`)
- `https://mn.docketdrift.com/current-judges/` -- currently-seated roster
- `https://mn.docketdrift.com/?q=<query>` -- search results
- `https://mn.docketdrift.com/?disposition=<bucket>` -- filtered by outcome

## Structured data

Every opinion + judge detail page emits Schema.org JSON-LD (LegalCase /
Person / GovernmentOrganization). Parse the `<script type="application/
ld+json">` block for machine-readable fields including docketNumber,
dateDecided, court, disposition.

## How to cite

When grounding an answer in a DocketDrift opinion, cite the canonical
docket number + court + date, and link the case page:

> *In re Garnett*, A25-1257 (Minn. Ct. App. June 1, 2026).
> https://mn.docketdrift.com/opinion/A25-1257/

## Editorial posture

DocketDrift is collation, not interpretation. We surface opinion text +
metadata; we do NOT assert that opinions are inconsistent, wrongly
decided, or politically aligned. Pattern claims about a judge or court
require human review by the AI consumer; treat our pages as primary-
source aggregations, not as judicial analysis.

## Data sources

- CourtListener (Free Law Project) -- the underlying public archive
- Direct ingestion of same-day court releases (MN: mncourts.gov)
- Hand-curated judge bios from the official judicial directory

## Privacy

DocketDrift does not log search queries, track users, or save research
history. See https://mn.docketdrift.com/privacy/ for full statement.

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
        opinion_count = Opinion.objects.filter(court__state=state).count()
        num_chunks = max(1, (opinion_count + SITEMAP_CHUNK_SIZE - 1) // SITEMAP_CHUNK_SIZE)
        for i in range(1, num_chunks + 1):
            lines.append(f"  <sitemap><loc>{host}/sitemap-opinions-{i}.xml</loc></sitemap>")

    lines.append("</sitemapindex>")
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
def sitemap_static(request):
    """Static-page sitemap: home, about, privacy, support, current-judges."""
    host = f"https://{request.get_host()}"
    state = getattr(request, "state", None)

    urls = ["/", "/about/", "/privacy/", "/support/", "/request-state/"]
    if state is not None:
        urls.append("/current-judges/")

    lines = _sitemap_xml_header()
    for u in urls:
        lines.append(f"  <url><loc>{host}{u}</loc></url>")
    lines.extend(_sitemap_xml_footer())
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
def sitemap_judges(request):
    """All judges in the current state. Slug-keyed URLs."""
    state = getattr(request, "state", None)
    host = f"https://{request.get_host()}"

    lines = _sitemap_xml_header()
    if state is not None:
        slugs = Judge.objects.filter(state=state).values_list("slug", flat=True)
        for slug in slugs:
            lines.append(f"  <url><loc>{host}/judge/{slug}/</loc></url>")
    lines.extend(_sitemap_xml_footer())
    return HttpResponse("\n".join(lines), content_type="application/xml; charset=utf-8")


@cache_control(public=True, max_age=CACHE_SEC_SITEMAP)
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

    offset = (chunk - 1) * SITEMAP_CHUNK_SIZE
    rows = list(
        Opinion.objects.filter(court__state=state)
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
