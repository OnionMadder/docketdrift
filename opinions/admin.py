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
    TagSuggestion,
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


@admin.register(TagSuggestion)
class TagSuggestionAdmin(admin.ModelAdmin):
    """Stopgap list view of tag suggestions emitted by suggest_tags.

    Sprint 1C replaces this with a high-throughput HTMX review surface
    (one-click accept/reject, in-place row swap). For now this gives
    the editor a way to inspect what the cosine math is surfacing --
    sort by confidence to find the borderline calls, filter by status
    to focus on the pending queue.
    """
    list_display = (
        "opinion_link",
        "tag",
        "confidence",
        "status",
        "reviewed_at",
        "reviewed_by",
        "created_at",
    )
    list_filter = ("status", "tag__category", "tag")
    search_fields = ("opinion__case_number", "opinion__title", "tag__slug", "tag__label")
    list_select_related = ("opinion", "opinion__court", "tag")
    ordering = ("-confidence",)
    readonly_fields = ("opinion", "tag", "confidence", "created_at")
    # Don't compute the full count -- the table grows to ~300K rows.
    show_full_result_count = False
    actions = ["accept_selected", "reject_selected"]

    @admin.display(description="opinion", ordering="opinion__case_number")
    def opinion_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse
        url = reverse("admin:opinions_opinion_change", args=[obj.opinion_id])
        return format_html(
            '<a href="{}">{}</a> <small style="color:#888">{}</small>',
            url,
            obj.opinion.case_number,
            (obj.opinion.title or "")[:60],
        )

    @admin.action(description="Accept selected (attach tag to opinion)")
    def accept_selected(self, request, queryset):
        from django.utils import timezone
        now = timezone.now()
        accepted = 0
        for sug in queryset.select_related("opinion", "tag"):
            sug.opinion.tags.add(sug.tag)
            sug.status = TagSuggestion.Status.ACCEPTED
            sug.reviewed_at = now
            sug.reviewed_by = request.user.username
            sug.save(update_fields=["status", "reviewed_at", "reviewed_by"])
            accepted += 1
        self.message_user(request, f"Accepted {accepted} suggestion(s).")

    @admin.action(description="Reject selected")
    def reject_selected(self, request, queryset):
        from django.utils import timezone
        n = queryset.update(
            status=TagSuggestion.Status.REJECTED,
            reviewed_at=timezone.now(),
            reviewed_by=request.user.username,
        )
        self.message_user(request, f"Rejected {n} suggestion(s).")


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
    # courtlistener_id is editable inline so blank rows can be filled in
    # without opening each change-form -- pair it with the cl_search_link
    # column for a click-out-then-paste-back workflow.
    list_display = ("full_name", "state", "status", "is_currently_seated",
                    "courtlistener_id", "cl_search_link", "view_on_site_link")
    list_editable = ("courtlistener_id",)
    list_filter = ("state", "status", "is_currently_seated")
    search_fields = ("full_name", "slug", "courtlistener_id")
    list_select_related = ("state", "court")
    list_per_page = 50

    @admin.display(description="lookup")
    def cl_search_link(self, obj):
        """Per-row link to CourtListener's people-search prefilled with
        this judge's name. Click -> CL opens in a new tab with the
        relevant person results -> grab the numeric id from the URL
        and paste into the inline courtlistener_id cell."""
        from django.utils.html import format_html
        from urllib.parse import quote_plus
        if obj.courtlistener_id:
            return format_html(
                '<span style="color:#888">resolved</span>'
            )
        return format_html(
            '<a href="https://www.courtlistener.com/person/?q={}" '
            'target="_blank" rel="noopener" '
            'title="Search CourtListener for {}">'
            'search CL &rarr;</a>',
            quote_plus(obj.full_name or ""),
            obj.full_name,
        )

    @admin.display(description="live page")
    def view_on_site_link(self, obj):
        """Per-row link to the public dossier on the state subdomain.
        Same destination as the top-right "View on site" button on the
        change form, but accessible from the changelist without opening
        the form -- useful when scrubbing a list of judges and just
        wanting to spot-check each live page."""
        from django.utils.html import format_html
        if not obj.slug or not obj.state_id:
            return ""
        return format_html(
            '<a href="https://{}.docketdrift.com/judge/{}/" '
            'target="_blank" rel="noopener">view &rarr;</a>',
            obj.state.slug, obj.slug,
        )

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


class TagSuggestionInline(admin.TabularInline):
    """In-context AI tag suggestions for this opinion.

    Shows everything ``suggest_tags`` produced for this opinion -- both
    pending and decided -- ordered by confidence so the strongest
    candidates surface first. Lets an editor accept or reject inline
    without leaving the opinion change form (the alternative is the
    standalone /admin/opinions/tagsuggestion/ queue, which is better for
    bulk-review sessions).

    The (opinion, tag, confidence) columns are read-only because
    they're computed by suggest_tags. Only ``status`` is editable -- the
    bulk-action accept/reject from TagSuggestionAdmin doesn't apply
    here, so the editor flips the choice manually and the inline save
    persists it.
    """
    model = TagSuggestion
    extra = 0
    fields = ("tag", "confidence", "status", "reviewed_by", "reviewed_at")
    readonly_fields = ("tag", "confidence", "reviewed_at")
    can_delete = False
    show_change_link = False
    ordering = ("-confidence",)
    verbose_name = "tag suggestion"
    verbose_name_plural = "Suggested tags (AI candidates)"

    def has_add_permission(self, request, obj=None):
        # Suggestions are computed by suggest_tags; editors don't add by hand.
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("tag")
        )


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


@admin.action(description="Revert selected to Processed (un-review)")
def mark_ai_only(modeladmin, request, queryset):
    n = queryset.update(
        review_status=Opinion.ReviewStatus.AI_ONLY,
        reviewed_by="",
        reviewed_at=None,
    )
    modeladmin.message_user(request, f"{n} opinion(s) reverted to Processed.")


@admin.action(description="Re-run parser on selected (fill missing disposition + bucket)")
def rerun_parser(modeladmin, request, queryset):
    """Run the state parser on selected opinions to fill missing
    ``disposition`` / ``disposition_bucket``.

    Only touches rows where disposition is currently empty -- never
    overwrites a human-entered or previously-parsed value. The bulk
    backfill management command exists for the corpus-wide pass; this
    action is for one-off cleanup while reading individual opinions.
    """
    from opinions.parsing import parse as parse_opinion
    from opinions.utils import compute_disposition_bucket as _bucket

    filled = skipped = 0
    qs = queryset.select_related("court__state")
    for op in qs:
        if op.disposition:
            skipped += 1
            continue
        if not op.raw_text:
            skipped += 1
            continue
        result = parse_opinion(op.court.state_id, op.raw_text)
        if result is None or not result.disposition:
            skipped += 1
            continue
        Opinion.objects.filter(pk=op.pk).update(
            disposition=result.disposition[:128],
            disposition_bucket=_bucket(result.disposition),
        )
        filled += 1
    modeladmin.message_user(
        request,
        f"Re-ran parser: {filled} disposition(s) filled; {skipped} skipped "
        "(already populated, no raw_text, or no parser match).",
    )


@admin.action(description="Auto-flag ALL pre-1849 opinions (data-quality triage)")
def flag_pre_1849(modeladmin, request, queryset):
    """One-shot data-quality triage: anything released before MN Territory
    was even organized (March 3, 1849) is almost certainly misdated -- the
    actual release year is hidden in the case number or the text. Flagging
    them creates a clean review queue under ?review_status__exact=flagged.

    Operates on the FULL corpus, not just the changelist selection,
    because the user normally doesn't have all suspicious rows visible
    at once; the goal is "build my triage queue in one click".
    """
    from datetime import date
    n = Opinion.objects.filter(
        release_date__lt=date(1849, 1, 1),
    ).update(review_status=Opinion.ReviewStatus.FLAGGED)
    modeladmin.message_user(
        request,
        f"{n} pre-1849 opinion(s) flagged for review. "
        f"Find them at ?review_status__exact=flagged.",
    )


class SuspiciousDateFilter(admin.SimpleListFilter):
    """Sidebar filter for surfacing likely-misdated opinions.

    MN Territorial Supreme Court started March 1849 (statehood 1858).
    Anything older than 1849 is almost certainly a CL data-quality
    error where the actual year was misread into release_date. 1849-1899
    deserves verification but might be real territorial / early-state
    jurisprudence.
    """
    title = "Date sanity"
    parameter_name = "date_sanity"

    def lookups(self, request, model_admin):
        return [
            ("pre_1849", "Pre-1849 (likely misdated)"),
            ("pre_1900", "Pre-1900 (worth verifying)"),
            ("future", "Future-dated (impossible)"),
        ]

    def queryset(self, request, queryset):
        from datetime import date
        if self.value() == "pre_1849":
            return queryset.filter(release_date__lt=date(1849, 1, 1))
        if self.value() == "pre_1900":
            return queryset.filter(release_date__lt=date(1900, 1, 1))
        if self.value() == "future":
            return queryset.filter(release_date__gt=date.today())
        return queryset


class HasBodyFilter(admin.SimpleListFilter):
    """Filter on whether raw_text was successfully extracted.

    Empty raw_text + populated metadata = ingest happened but the body
    didn't make it through the CL fallback ladder (plain_text → xml_harvard
    → html_*). These are the rows where editorial review is most useful
    because the parser has nothing to work with.
    """
    title = "Body text"
    parameter_name = "body"

    def lookups(self, request, model_admin):
        return [
            ("none", "No body text"),
            ("yes", "Has body text"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "none":
            return queryset.filter(raw_text="")
        if self.value() == "yes":
            return queryset.exclude(raw_text="")
        return queryset


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
        SuspiciousDateFilter,
        HasBodyFilter,
        "is_precedential",
        "release_date",
        "disposition",
    )
    search_fields = ("case_number", "title", "courtlistener_id", "disposition")
    date_hierarchy = "release_date"
    readonly_fields = ("reviewed_at",)
    filter_horizontal = ("tags",)
    inlines = [
        PanelVoteInline,
        TagSuggestionInline,
        OpinionHoldingInline,
        ParseLogInline,
    ]
    actions = [mark_reviewed, mark_flagged, mark_ai_only, rerun_parser, flag_pre_1849]
    # 60K opinions in the corpus -- without these, the changelist tries
    # to count + render too much per request.
    list_per_page = 50
    show_full_result_count = False  # skip the unfiltered-count query

    def get_queryset(self, request):
        # select_related the FKs that list_display renders so the
        # changelist makes one query instead of N+1, and DEFER the two
        # giant TEXT columns (raw_text + html_content, ~50-100KB each)
        # since list_display + list_filter never touch them. Without the
        # defer, a 50-row page pulls ~5MB of body text across the wire
        # per request -- under embed_opinions contention that hits the
        # "Lost connection to MySQL server during query" / (2013)
        # mid-query disconnect described in CLAUDE.md, and a save-then-
        # redirect lands on a 500 page even though the save itself
        # succeeded.
        return (
            super()
            .get_queryset(request)
            .select_related("court", "court__state")
            .defer("raw_text", "html_content")
        )

    def changelist_view(self, request, extra_context=None):
        """State-then-year picker landing when no filter is active.

        With 60K (MN) + 38K (AZ) + 20K (NH) opinions in the corpus, the
        bare changelist is unusably slow AND useless -- there's nothing
        meaningful to do with 120K rows. So when there are no query
        params, render a per-state year-grid: each state section lists
        its years as clickable tiles, each tile filters the changelist
        to (state, year). One click lands on a tight ~1-5K-row view.

        Clicking ANY filter / year in date_hierarchy / etc keeps the
        normal changelist behavior (the redirect only fires on the bare
        URL).
        """
        if request.GET:
            return super().changelist_view(request, extra_context=extra_context)

        from django.db.models import Count
        from django.db.models.functions import ExtractYear
        from django.template.response import TemplateResponse

        rows = list(
            Opinion.objects
            .filter(release_date__isnull=False)
            .annotate(year=ExtractYear("release_date"))
            .values("court__state__code", "court__state__name", "year")
            .annotate(count=Count("id"))
            .order_by("court__state__code", "-year")
        )

        # Group rows by state for the template; preserve state ordering
        # by code, year ordering by recency.
        states: list[dict] = []
        by_code: dict[str, dict] = {}
        for r in rows:
            code = r["court__state__code"]
            entry = by_code.get(code)
            if entry is None:
                entry = {
                    "code": code,
                    "name": r["court__state__name"],
                    "years": [],
                    "total": 0,
                }
                by_code[code] = entry
                states.append(entry)
            entry["years"].append({"year": r["year"], "count": r["count"]})
            entry["total"] += r["count"]

        grand_total = sum(s["total"] for s in states)

        context = {
            **self.admin_site.each_context(request),
            "title": "Opinions — browse by state and year",
            "opts": self.model._meta,
            "states": states,
            "total": grand_total,
            "has_unfiltered_data": bool(states),
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
