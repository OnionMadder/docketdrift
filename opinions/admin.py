"""Django admin for DocketDrift opinions."""
from django.contrib import admin

from opinions.models import (
    Court,
    Judge,
    Opinion,
    OpinionHolding,
    PanelVote,
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


@admin.register(Opinion)
class OpinionAdmin(admin.ModelAdmin):
    list_display = (
        "case_number",
        "court",
        "release_date",
        "is_precedential",
        "title_excerpt",
    )
    list_filter = ("court__state", "court", "is_precedential", "release_date")
    search_fields = ("case_number", "title", "courtlistener_id")
    date_hierarchy = "release_date"
    inlines = [PanelVoteInline, OpinionHoldingInline]

    @admin.display(description="Title")
    def title_excerpt(self, obj):
        return (obj.title or "")[:80]


@admin.register(OpinionHolding)
class OpinionHoldingAdmin(admin.ModelAdmin):
    list_display = ("opinion", "statute_cited", "holding_direction")
    list_filter = ("holding_direction",)
    search_fields = ("statute_cited", "holding_text")
