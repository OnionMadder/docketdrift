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

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from opinions.models import Opinion, Tag, TagSuggestion


REVIEW_PAGE_SIZE = 25


@staff_member_required
def tag_review(request):
    """Render the bulk tag-suggestion review grid.

    Query params:
      tag=<slug>          filter to one tag's queue
      category=<cat>      filter to one tag-category (doctrine/subject/...)
      min_confidence=0.3  drop suggestions below this score
      status=<status>     review state to show (default pending)
      page=N              pagination
    """
    status = request.GET.get("status", TagSuggestion.Status.PENDING)
    qs = (
        TagSuggestion.objects
        .filter(status=status)
        .select_related("opinion", "opinion__court", "tag")
        .order_by("-confidence")
    )

    tag_filter = request.GET.get("tag", "").strip()
    if tag_filter:
        qs = qs.filter(tag__slug=tag_filter)

    category_filter = request.GET.get("category", "").strip()
    if category_filter:
        qs = qs.filter(tag__category=category_filter)

    min_confidence = request.GET.get("min_confidence", "").strip()
    if min_confidence:
        try:
            qs = qs.filter(confidence__gte=float(min_confidence))
        except ValueError:
            pass  # silently ignore malformed input

    paginator = Paginator(qs, REVIEW_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Stats header. We deliberately compute these on every render rather
    # than caching because (a) the numbers SHOULD update as the maintainer
    # reviews and (b) at 20K-row scale the queries take <50ms each.
    status_counts = dict(
        TagSuggestion.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n")
    )

    tag_choices = (
        Tag.objects.exclude(suggestions__isnull=True)
        .annotate(n=Count("suggestions", filter=Q(suggestions__status=TagSuggestion.Status.PENDING)))
        .filter(n__gt=0)
        .order_by("category", "label")
    )

    return render(request, "opinions/admin/tag_review.html", {
        "title": "Tag suggestions review",
        "page_obj": page_obj,
        "status_counts": status_counts,
        "tag_choices": tag_choices,
        "category_choices": Tag.Category.choices,
        "active_tag": tag_filter,
        "active_category": category_filter,
        "active_status": status,
        "active_min_confidence": min_confidence,
        # Hand the threshold values down so the row template can
        # color the confidence bar relative to the auto-apply boundary.
        "review_threshold": _get_setting("TAG_SUGGESTION_REVIEW_THRESHOLD", 0.25),
        "auto_apply_threshold": _get_setting("TAG_SUGGESTION_AUTO_APPLY_THRESHOLD", 0.40),
    })


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
