"""Django admin for DocketDrift opinions."""
from django.contrib import admin

from opinions.models import (
    Court,
    Judge,
    Opinion,
    OpinionHolding,
    PanelVote,
    ParseLog,
    QueryEmbedding,
    State,
    StateRequest,
    Tag,
)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    """Controlled-vocabulary tag editor.

    Add a new tag here when manual review surfaces a pattern that the
    starter vocabulary doesn't cover. Slug should be URL-safe (lowercase,
    hyphens); category groups the tag on the public browse page.
    """
    list_display = ("label", "slug", "category", "opinion_count", "created_at")
    list_filter = ("category",)
    search_fields = ("slug", "label", "description")
    prepopulated_fields = {"slug": ("label",)}
    ordering = ("category", "label")

    @admin.display(description="opinions", ordering=None)
    def opinion_count(self, obj):
        return obj.opinions.count()


@admin.register(QueryEmbedding)
class QueryEmbeddingAdmin(admin.ModelAdmin):
    """Read-only view into the semantic-search query cache.

    Useful for spot-checking: what are users searching for, and how
    often is the cache earning its keep? hit_count > 1 means we saved
    a Voyage API call.
    """
    list_display = ("query", "hit_count", "created_at", "last_used_at")
    list_filter = ("created_at",)
    search_fields = ("query",)
    readonly_fields = ("query", "embedding_json", "hit_count", "created_at", "last_used_at")
    ordering = ("-hit_count",)


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "slug", "is_live")
    list_filter = ("is_live",)
    search_fields = ("code", "name")


def _render_state_picker(modeladmin, request, *, title, picker_lead, row_label,
                          state_count_relation, quick_filters=None):
    """Shared state-picker landing for Court + Judge admins.

    ``state_count_relation`` is the reverse-FK attribute on State (e.g.
    'courts', 'judges') so we can annotate per-state counts in a single
    aggregate query. Each tile links to the standard changelist filtered
    to that state.
    """
    from django.db.models import Count
    from django.template.response import TemplateResponse

    states = list(
        State.objects
        .annotate(_count=Count(state_count_relation))
        .filter(_count__gt=0)
        .order_by("name")
    )
    entries = [
        {
            "state": s,
            "count": s._count,
            "querystring": f"?state__code__exact={s.code}",
        }
        for s in states
    ]

    context = {
        **modeladmin.admin_site.each_context(request),
        "title": title,
        "picker_lead": picker_lead,
        "row_label": row_label,
        "opts": modeladmin.model._meta,
        "states": entries,
        "quick_filters": quick_filters or [],
    }
    return TemplateResponse(
        request,
        "admin/opinions/state_picker.html",
        context,
    )


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    list_display = ("name", "state", "level", "courtlistener_id")
    list_filter = ("state", "level")
    search_fields = ("name", "courtlistener_id")
    list_select_related = ("state",)

    def changelist_view(self, request, extra_context=None):
        # The bare URL renders a per-state landing so the page reads as
        # "states with courts" rather than an intermixed list. ?all=1
        # bypasses the picker and shows the flat changelist.
        if request.GET and "all" not in request.GET:
            return super().changelist_view(request, extra_context=extra_context)
        if request.GET.get("all"):
            # Strip the all=1 marker so list_filter's GET handling stays clean.
            return super().changelist_view(request, extra_context=extra_context)
        return _render_state_picker(
            self,
            request,
            title="Courts — browse by state",
            picker_lead="Click a state to see its appellate courts.",
            row_label="court",
            state_count_relation="courts",
        )


@admin.register(Judge)
class JudgeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "state", "status", "is_currently_seated", "courtlistener_id")
    list_filter = ("state", "status", "is_currently_seated")
    search_fields = ("full_name", "slug", "courtlistener_id")
    list_select_related = ("state", "court")
    list_per_page = 50

    def changelist_view(self, request, extra_context=None):
        # Per-state landing so the 125+ judges (24 currently-seated MN
        # + 100+ historical) aren't all dumped intermixed on entry.
        if request.GET and "all" not in request.GET:
            return super().changelist_view(request, extra_context=extra_context)
        if request.GET.get("all"):
            return super().changelist_view(request, extra_context=extra_context)
        return _render_state_picker(
            self,
            request,
            title="Judges — browse by state",
            picker_lead=(
                "Click a state to see its judges. From there, filter "
                "further by currently-seated vs. historical, or by status."
            ),
            row_label="judge",
            state_count_relation="judges",
            quick_filters=[
                {"label": "Currently seated", "querystring": "?is_currently_seated__exact=1"},
                {"label": "Historical only", "querystring": "?is_currently_seated__exact=0"},
                {"label": "Without CL id", "querystring": "?courtlistener_id__exact="},
            ],
        )


class PanelVoteInline(admin.TabularInline):
    model = PanelVote
    extra = 0
    autocomplete_fields = ("judge",)


class OpinionHoldingInline(admin.StackedInline):
    model = OpinionHolding
    extra = 0
    fields = ("statute_cited", "legal_issue_tag", "holding_direction", "holding_text")


class ParseLogInline(admin.TabularInline):
    model = ParseLog
    extra = 0
    fields = ("parser_state", "parser_version", "missing_fields", "duration_ms", "created_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.action(description="Mark selected as human-reviewed")
def mark_reviewed(modeladmin, request, queryset):
    """Bulk-flip selected opinions to REVIEWED, stamping editor + timestamp.

    Uses queryset.update() to skip Opinion.save() (and thus the parser
    save-hook) -- intentional: we're only updating editorial metadata,
    no need to re-run parse logic on every selected row.
    """
    from django.utils import timezone
    n = queryset.update(
        review_status=Opinion.ReviewStatus.REVIEWED,
        reviewed_by=request.user.username,
        reviewed_at=timezone.now(),
    )
    modeladmin.message_user(request, f"{n} opinion(s) marked as human-reviewed.")


@admin.action(description="Flag selected for review")
def mark_flagged(modeladmin, request, queryset):
    n = queryset.update(review_status=Opinion.ReviewStatus.FLAGGED)
    modeladmin.message_user(request, f"{n} opinion(s) flagged for review.")


@admin.action(description="Revert selected to AI-processed only")
def mark_ai_only(modeladmin, request, queryset):
    n = queryset.update(
        review_status=Opinion.ReviewStatus.AI_ONLY,
        reviewed_by="",
        reviewed_at=None,
    )
    modeladmin.message_user(request, f"{n} opinion(s) reverted to AI-processed.")


@admin.register(Opinion)
class OpinionAdmin(admin.ModelAdmin):
    list_display = (
        "case_number",
        "court",
        "release_date",
        "review_status",
        "is_precedential",
        "disposition",
        "title_excerpt",
    )
    list_filter = (
        "review_status",
        "court__state",
        "court",
        "is_precedential",
        "release_date",
        "disposition",
    )
    search_fields = ("case_number", "title", "courtlistener_id", "disposition")
    date_hierarchy = "release_date"
    readonly_fields = ("reviewed_at",)
    filter_horizontal = ("tags",)
    inlines = [PanelVoteInline, OpinionHoldingInline, ParseLogInline]
    actions = [mark_reviewed, mark_flagged, mark_ai_only]
    # 60K opinions in the corpus -- without these, the changelist tries
    # to count + render too much per request.
    list_per_page = 50
    show_full_result_count = False  # skip the unfiltered-count query

    def get_queryset(self, request):
        # select_related the FKs that list_display renders so the
        # changelist makes one query instead of N+1.
        return (
            super()
            .get_queryset(request)
            .select_related("court", "court__state")
        )

    def changelist_view(self, request, extra_context=None):
        """Year-picker landing when no filter is active.

        With 60K opinions, hitting /admin/opinions/opinion/ raw is slow
        AND useless -- there's nothing meaningful to do with a 60K-row
        list. So when there are no query params, we render a year-grid
        instead: each year is a clickable tile that drops into the
        normal changelist filtered to that year. Once filtered to
        ~1-3K opinions per year, the changelist renders fast and the
        editorial review workflow has natural drill-down.

        Clicking ANY filter or year in date_hierarchy keeps the normal
        changelist behavior (the redirect only fires on the bare URL).
        """
        if request.GET:
            return super().changelist_view(request, extra_context=extra_context)

        from django.db.models import Count
        from django.db.models.functions import ExtractYear
        from django.template.response import TemplateResponse

        years = list(
            Opinion.objects
            .filter(release_date__isnull=False)
            .annotate(year=ExtractYear("release_date"))
            .values("year")
            .annotate(count=Count("id"))
            .order_by("-year")
        )
        total = sum(y["count"] for y in years)

        context = {
            **self.admin_site.each_context(request),
            "title": "Opinions — browse by year",
            "opts": self.model._meta,
            "years": years,
            "total": total,
            "has_unfiltered_data": bool(years),
        }
        return TemplateResponse(
            request,
            "admin/opinions/opinion/year_picker.html",
            context,
        )

    fieldsets = (
        (None, {
            "fields": (
                "court",
                "case_number",
                "title",
                "release_date",
                "is_precedential",
                "disposition",
                "source_url",
                "courtlistener_id",
            ),
        }),
        ("Body", {
            "fields": ("pdf_file", "raw_text", "html_content", "sha256"),
        }),
        ("Editorial review", {
            "fields": ("review_status", "reviewed_by", "reviewed_at", "review_notes", "tags"),
            "description": (
                "Use the bulk actions on the changelist for fast review passes. "
                "Editing review_status here will auto-stamp reviewed_at when saved. "
                "Tags are the controlled-vocabulary editorial layer -- add via the "
                "Tag changelist; apply via the multi-select widget below."
            ),
        }),
    )

    @admin.display(description="Title")
    def title_excerpt(self, obj):
        return (obj.title or "")[:80]


@admin.register(OpinionHolding)
class OpinionHoldingAdmin(admin.ModelAdmin):
    list_display = ("opinion", "statute_cited", "holding_direction")
    list_filter = ("holding_direction",)
    search_fields = ("statute_cited", "holding_text")


@admin.register(StateRequest)
class StateRequestAdmin(admin.ModelAdmin):
    """Reader requests for a state's appellate corpus.

    Grouping by state_name in the changelist surfaces which states have
    the most reader demand -- the queue this drives is "which state to
    embed next".
    """
    list_display = ("state_name", "email_short", "created_at")
    list_filter = ("created_at",)
    search_fields = ("state_name", "email", "notes")
    readonly_fields = ("created_at", "ip_address")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    @admin.display(description="Email")
    def email_short(self, obj):
        return obj.email or "—"


@admin.register(ParseLog)
class ParseLogAdmin(admin.ModelAdmin):
    list_display = (
        "opinion",
        "parser_state",
        "parser_version",
        "missing_count",
        "duration_ms",
        "created_at",
    )
    list_filter = ("parser_state", "parser_version", "created_at")
    search_fields = ("opinion__case_number", "opinion__title")
    date_hierarchy = "created_at"
    readonly_fields = (
        "opinion",
        "parser_state",
        "parser_version",
        "extracted",
        "missing_fields",
        "raw_text_sha256",
        "duration_ms",
        "created_at",
    )

    @admin.display(description="missing")
    def missing_count(self, obj):
        return len(obj.missing_fields or [])
