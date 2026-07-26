"""Top-level URL configuration."""
from django.contrib import admin
from django.urls import include, path

from opinions.admin_views import tag_review, tag_review_action, tag_review_bulk

urlpatterns = [
    # Custom admin pages live in front of Django's auto-generated admin
    # URL routes so they can use the same /admin/ URL prefix without
    # colliding with the model-changelist views. ``tag_review`` is the
    # HTMX-powered bulk tag-suggestion review surface; see
    # ``opinions/admin_views.py``.
    path(
        "admin/opinions/tag-review/",
        tag_review,
        name="admin_tag_review",
    ),
    path(
        "admin/opinions/tag-review/bulk/",
        tag_review_bulk,
        name="admin_tag_review_bulk",
    ),
    path(
        "admin/opinions/tag-review/<int:suggestion_id>/<str:action>/",
        tag_review_action,
        name="admin_tag_review_action",
    ),
    path("admin/", admin.site.urls),
    path("", include("opinions.urls")),
]
