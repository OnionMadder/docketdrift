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
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render

from opinions.models import Opinion, State


def home(request):
    """Apex state-picker when no subdomain matches; per-state landing otherwise."""
    state = getattr(request, "state", None)
    search_q = (request.GET.get("q") or "").strip()

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
    )
    if search_q:
        qs = qs.filter(
            Q(case_number__icontains=search_q)
            | Q(title__icontains=search_q)
            | Q(raw_text__icontains=search_q)
        )
    total_count = qs.count()
    opinions = list(qs.order_by("-release_date")[:100])
    return render(request, "opinions/state_home.html", {
        "state": state,
        "opinions": opinions,
        "total_count": total_count,
        "active_nav": "opinions",
        "search_q": search_q,
    })


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


def about(request):
    return render(request, "opinions/about.html", {
        "active_nav": "about",
    })
