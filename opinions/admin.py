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
)


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


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    list_display = ("name", "state", "level", "courtlistener_id")
    list_filter = ("state", "level")
    search_fields = ("name", "courtlistener_id")


@admin.register(Judge)
class JudgeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "state", "status", "courtlistener_id")
    list_filter = ("state", "status")
    search_fields = ("full_name", "slug", "courtlistener_id")


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
    inlines = [PanelVoteInline, OpinionHoldingInline, ParseLogInline]
    actions = [mark_reviewed, mark_flagged, mark_ai_only]

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
            "fields": ("review_status", "reviewed_by", "reviewed_at", "review_notes"),
            "description": (
                "Use the bulk actions on the changelist for fast review passes. "
                "Editing review_status here will auto-stamp reviewed_at when saved."
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
