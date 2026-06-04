"""Django admin for DocketDrift opinions."""
from django.contrib import admin

from opinions.models import (
    Court,
    Judge,
    Opinion,
    OpinionHolding,
    PanelVote,
    ParseLog,
    State,
)


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


@admin.register(Opinion)
class OpinionAdmin(admin.ModelAdmin):
    list_display = (
        "case_number",
        "court",
        "release_date",
        "is_precedential",
        "disposition",
        "title_excerpt",
    )
    list_filter = ("court__state", "court", "is_precedential", "release_date", "disposition")
    search_fields = ("case_number", "title", "courtlistener_id", "disposition")
    date_hierarchy = "release_date"
    inlines = [PanelVoteInline, OpinionHoldingInline, ParseLogInline]

    @admin.display(description="Title")
    def title_excerpt(self, obj):
        return (obj.title or "")[:80]


@admin.register(OpinionHolding)
class OpinionHoldingAdmin(admin.ModelAdmin):
    list_display = ("opinion", "statute_cited", "holding_direction")
    list_filter = ("holding_direction",)
    search_fields = ("statute_cited", "holding_text")


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
