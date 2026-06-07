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
from django.db import connection
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import cache_control

from opinions.models import Judge, Opinion, State


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


@cache_control(public=True, max_age=CACHE_SEC_HOME)
def home(request):
    """Apex state-picker when no subdomain matches; per-state landing otherwise.

    The state landing has two display modes:

    - DEFAULT (no filter/search): shows ``HOME_LANDING_SIZE`` most recent
      opinions, no pagination, with a "use search to dig deeper" prompt.
      Mirrors how casual visitors actually use the site -- they want to
      see what's new, then search for the specific thing they're after.
    - FILTERED/SEARCHED (``?q=`` or ``?disposition=``): full pagination
      at ``HOME_PAGE_SIZE`` per page. This is the power-user view.

    Switching modes only on the presence of a query param keeps the
    contract simple -- one URL grammar, two render modes.
    """
    state = getattr(request, "state", None)
    search_q = (request.GET.get("q") or "").strip()
    disp_filter = (request.GET.get("disposition") or "").strip().lower()

    if state is None:
        live = list(State.objects.filter(is_live=True).order_by("name"))
        for s in live:
            s.opinion_count = Opinion.objects.filter(court__state=s).count()
        return render(request, "opinions/apex.html", {
            "states": live,
            "active_nav": "opinions",
            "search_q": search_q,
        })

    qs = (
        Opinion.objects.filter(court__state=state)
        .select_related("court")
        .order_by("-release_date")
    )
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
        "page_obj": page_obj,
        "total_count": total_count,
        "is_filtered": is_filtered,
        "active_nav": "opinions",
        "search_q": search_q,
        "disp_filter": disp_filter,
        "disp_label": disp_label,
    })


@cache_control(public=True, max_age=CACHE_SEC_DETAIL)
def opinion_detail(request, case_number):
    """Single-opinion detail. Scoped to the current state subdomain when set."""
    state = getattr(request, "state", None)
    qs = Opinion.objects.select_related("court", "court__state")
    if state is not None:
        qs = qs.filter(court__state=state)
    try:
        opinion = qs.get(case_number=case_number)
    except Opinion.DoesNotExist:
        raise Http404("Opinion not found")
    return render(request, "opinions/opinion_detail.html", {
        "opinion": opinion,
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


@cache_control(public=True, max_age=CACHE_SEC_DETAIL)
def judge_detail(request, slug):
    """Per-judge dossier page. State-scoped to keep slugs unambiguous."""
    state = getattr(request, "state", None)
    qs = Judge.objects.select_related("state", "court")
    if state is not None:
        qs = qs.filter(state=state)
    try:
        judge = qs.get(slug=slug)
    except Judge.DoesNotExist:
        raise Http404("Judge not found")
    return render(request, "opinions/judge_detail.html", {
        "judge": judge,
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
