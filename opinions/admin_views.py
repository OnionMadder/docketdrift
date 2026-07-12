"""Custom admin views outside Django's auto-generated admin URLs.

Today: the HTMX-powered bulk tag-suggestion review surface at
``/admin/opinions/tag-review/``. The Django admin's auto-generated
changelist is fine for one-off lookups but choked the moment we had
20K pending suggestions to review -- each click reloaded the whole
page, the maintainer wanted dozens of decisions per minute, not
dozens per hour.

The HTMX flow:

1. ``tag_review`` renders a paged grid of pending TagSuggestion rows
   with Accept / Reject buttons on each.
2. Each button POSTs to ``tag_review_action`` via ``hx-post``.
3. The endpoint applies the decision (accept = attach tag to opinion;
   reject = mark as REJECTED) and returns a tiny "decision recorded"
   fragment.
4. HTMX swaps the outgoing row's body with the fragment in-place; the
   page doesn't reload, and the maintainer's flow stays intact.

CSRF: HTMX picks the token off the page-level meta tag. The admin
templates extend ``admin/base_site.html`` which already emits a
``{% csrf_token %}`` somewhere; we re-emit it in a meta tag at the
top of the review template so ``hx-headers`` can read it cleanly.

Auth: ``@staff_member_required`` -- only Django staff users. The same
gate the admin app uses, so existing superuser sessions work without
a second login.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from opinions.models import Court, Opinion, State, Tag, TagSuggestion


REVIEW_PAGE_SIZE = 25


def _suggestion_filters(request_data):
    """Parse the shared filter params (used by both the list + bulk views).

    Returns ``(filters_dict, opinion_ids_or_None)``. A state filter resolves to
    the state's OPINION ids, not court ids: filtering TagSuggestion by
    ``opinion__court_id__in`` joins the 2.75GB opinions table and the optimizer
    picks a corpus-scanning plan on bigger states (AZ timed out at 25s; NH was
    fine). ``opinion_id__in`` against a pre-resolved id set is a direct indexed
    lookup on tagsuggestion -- ~300ms even for AZ's 38K ids. See CLAUDE.md
    "aggregate over a court_id__in filter scans the corpus".
    """
    filters = {
        "tag": (request_data.get("tag") or "").strip(),
        "category": (request_data.get("category") or "").strip(),
        "state": (request_data.get("state") or "").strip().upper(),
        "min_confidence": (request_data.get("min_confidence") or "").strip(),
    }
    opinion_ids = None
    if filters["state"]:
        court_ids = Court.objects.filter(
            state__code=filters["state"]
        ).values_list("id", flat=True)
        opinion_ids = list(
            Opinion.objects.filter(court_id__in=list(court_ids))
            .values_list("id", flat=True)
        )
    return filters, opinion_ids


def _apply_suggestion_filters(qs, filters, opinion_ids):
    """Narrow a TagSuggestion queryset by the shared filters."""
    if filters["tag"]:
        qs = qs.filter(tag__slug=filters["tag"])
    if filters["category"]:
        qs = qs.filter(tag__category=filters["category"])
    if opinion_ids is not None:
        qs = qs.filter(opinion_id__in=opinion_ids)
    if filters["min_confidence"]:
        try:
            qs = qs.filter(confidence__gte=float(filters["min_confidence"]))
        except ValueError:
            pass  # silently ignore malformed input
    return qs


@staff_member_required
def tag_review(request):
    """Render the bulk tag-suggestion review grid.

    Query params:
      tag=<slug>          filter to one tag's queue
      category=<cat>      filter to one tag-category (doctrine/subject/...)
      state=<CODE>        filter to one state's opinions (MN/NH/AZ)
      min_confidence=0.3  drop suggestions below this score
      status=<status>     review state to show (default pending)
      page=N              pagination

    The queue is deliberately worked one *slice* at a time -- a single tag
    and/or a single state -- so a 50K global pile reads as a series of
    finishable piles with their own progress bar, not one bottomless list.
    """
    status = request.GET.get("status", TagSuggestion.Status.PENDING)
    filters, opinion_ids = _suggestion_filters(request.GET)

    qs = _apply_suggestion_filters(
        TagSuggestion.objects
        .filter(status=status)
        .select_related("opinion", "opinion__court", "tag")
        .defer("opinion__raw_text", "opinion__html_content")
        .order_by("-confidence"),
        filters, opinion_ids,
    )

    paginator = Paginator(qs, REVIEW_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Stats header. We deliberately compute these on every render rather
    # than caching because (a) the numbers SHOULD update as the maintainer
    # reviews and (b) at 20K-row scale the queries take <50ms each.
    status_counts = dict(
        TagSuggestion.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n")
    )
    pending = status_counts.get("pending", 0)
    resolved = (
        status_counts.get("accepted", 0)
        + status_counts.get("rejected", 0)
        + status_counts.get("auto_applied", 0)
    )
    overall_total = pending + resolved
    overall_pct = round(100 * resolved / overall_total) if overall_total else 0

    tag_choices = (
        Tag.objects.exclude(suggestions__isnull=True)
        .annotate(n=Count("suggestions", filter=Q(suggestions__status=TagSuggestion.Status.PENDING)))
        .filter(n__gt=0)
        .order_by("category", "label")
    )

    # Per-slice progress: only meaningful once the editor has narrowed to a
    # tag and/or a state. Turns "50K to go" into "you've cleared 120 of 450 in
    # THIS pile" -- a unit you can actually finish in a sitting.
    slice_active = bool(filters["tag"] or filters["state"])
    slice_total = slice_reviewed = slice_pending = slice_pct = 0
    if slice_active:
        slice_total = _apply_suggestion_filters(
            TagSuggestion.objects.all(), filters, opinion_ids
        ).count()
        slice_pending = _apply_suggestion_filters(
            TagSuggestion.objects.filter(status=TagSuggestion.Status.PENDING),
            filters, opinion_ids,
        ).count()
        slice_reviewed = slice_total - slice_pending
        slice_pct = round(100 * slice_reviewed / slice_total) if slice_total else 0

    return render(request, "opinions/admin/tag_review.html", {
        "title": "Tag suggestions review",
        "page_obj": page_obj,
        "status_counts": status_counts,
        "overall_total": overall_total,
        "overall_resolved": resolved,
        "overall_pct": overall_pct,
        "tag_choices": tag_choices,
        "category_choices": Tag.Category.choices,
        "states": list(State.objects.filter(is_live=True).order_by("name")),
        "active_tag": filters["tag"],
        "active_category": filters["category"],
        "active_state": filters["state"],
        "active_status": status,
        "active_min_confidence": filters["min_confidence"],
        "slice_active": slice_active,
        "slice_total": slice_total,
        "slice_reviewed": slice_reviewed,
        "slice_pending": slice_pending,
        "slice_pct": slice_pct,
        "bulk_done": (request.GET.get("done") or "").strip(),
        "bulk_act": (request.GET.get("act") or "").strip(),
        # Hand the threshold values down so the row template can
        # color the confidence bar relative to the auto-apply boundary.
        "review_threshold": _get_setting("TAG_SUGGESTION_REVIEW_THRESHOLD", 0.25),
        "auto_apply_threshold": _get_setting("TAG_SUGGESTION_AUTO_APPLY_THRESHOLD", 0.40),
    })


@staff_member_required
@require_http_methods(["POST"])
def tag_review_bulk(request):
    """Accept or reject every PENDING suggestion matching the current filters.

    The daunting part of a 50K queue is clicking each near-certain accept at
    the top of a confidence-sorted, single-tag pile. This clears the whole
    filtered slice in one action.

    Safety: bulk *accept* requires a narrowing filter (a tag or a
    min-confidence floor) so a stray click can't apply the entire
    low-confidence pile site-wide. Reject has no such gate (rejecting is
    non-destructive -- it just records the negative).
    """
    action = request.POST.get("action", "")
    filters, opinion_ids = _suggestion_filters(request.POST)

    if action == "accept" and not (filters["tag"] or filters["min_confidence"]):
        return HttpResponseBadRequest(
            "Bulk accept needs a tag or a minimum-confidence filter so it can't "
            "apply the entire low-confidence pile in one click."
        )

    qs = _apply_suggestion_filters(
        TagSuggestion.objects.filter(status=TagSuggestion.Status.PENDING),
        filters, opinion_ids,
    )

    now = timezone.now()
    user = request.user.username

    if action == "accept":
        # Attach the tags in bulk (one INSERT ... ON DUPLICATE KEY via
        # ignore_conflicts) instead of N per-row .add() calls, then flip the
        # suggestion rows to ACCEPTED. Both target status=PENDING, so the
        # values_list snapshot and the update hit the same rows.
        pairs = list(qs.values_list("opinion_id", "tag_id"))
        through = Opinion.tags.through
        through.objects.bulk_create(
            [through(opinion_id=o, tag_id=t) for o, t in pairs],
            ignore_conflicts=True,
        )
        n = qs.update(
            status=TagSuggestion.Status.ACCEPTED, reviewed_at=now, reviewed_by=user
        )
    elif action == "reject":
        n = qs.update(
            status=TagSuggestion.Status.REJECTED, reviewed_at=now, reviewed_by=user
        )
    else:
        return HttpResponseBadRequest("Unknown action")

    # Back to the same filtered slice with a flash count.
    params = {"status": "pending", "done": n, "act": action}
    for key in ("tag", "category", "state", "min_confidence"):
        if filters[key]:
            params[key] = filters[key]
    return redirect(f"{reverse('admin_tag_review')}?{urlencode(params)}")


@staff_member_required
@require_http_methods(["POST"])
def tag_review_action(request, suggestion_id: int, action: str):
    """Apply one accept/reject decision; return the row-replacement HTML.

    Called via HTMX from the per-row Accept/Reject buttons. The response
    is a tiny fragment template; HTMX swaps it into the row container in
    place of the original.
    """
    try:
        suggestion = (
            TagSuggestion.objects
            .select_related("opinion", "tag")
            .get(pk=suggestion_id)
        )
    except TagSuggestion.DoesNotExist:
        return HttpResponseNotFound("Suggestion not found")

    if action == "accept":
        suggestion.opinion.tags.add(suggestion.tag)
        suggestion.status = TagSuggestion.Status.ACCEPTED
    elif action == "reject":
        suggestion.status = TagSuggestion.Status.REJECTED
    else:
        return HttpResponseBadRequest("Unknown action")

    suggestion.reviewed_at = timezone.now()
    suggestion.reviewed_by = request.user.username
    suggestion.save(update_fields=["status", "reviewed_at", "reviewed_by"])

    return render(request, "opinions/admin/_tag_review_row_done.html", {
        "suggestion": suggestion,
        "action": action,
    })


def _get_setting(name: str, default):
    """Read a Django settings value with a safe default."""
    from django.conf import settings
    return getattr(settings, name, default)
