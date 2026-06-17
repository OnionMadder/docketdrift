"""URL routes for the opinions app."""
from django.urls import path

from opinions import views

app_name = "opinions"

urlpatterns = [
    path("", views.home, name="home"),
    path("opinions/", views.opinion_list, name="opinion_list"),
    path("about/", views.about, name="about"),
    path("how-we-differ/", views.how_we_differ, name="how_we_differ"),
    path("privacy/", views.privacy, name="privacy"),
    path("support/", views.support, name="support"),
    path("request-state/", views.request_state, name="request_state"),
    path("request-state/thanks/", views.request_state_thanks, name="request_state_thanks"),
    path("opinion/<str:case_number>/pdf/", views.opinion_pdf, name="opinion_pdf"),
    path("opinion/<str:case_number>/", views.opinion_detail, name="detail"),
    path("current-judges/", views.current_judges, name="current_judges"),
    path("judge/<slug:slug>/", views.judge_detail, name="judge_detail"),
    path("compare/judges/", views.judge_compare, name="judge_compare"),
    path("tag/", views.tag_index, name="tag_index"),
    path("tag/<slug:slug>/", views.tag_detail, name="tag_detail"),
    # Statute reference is a dot-separated slug (minn.stat.609.185), so the
    # URL pattern uses <str:> rather than <slug:>, which would reject dots.
    path("statute/<str:reference>/", views.statute_detail, name="statute_detail"),
    path("healthz", views.healthz, name="healthz"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("llms.txt", views.llms_txt, name="llms_txt"),
    path("sitemap.xml", views.sitemap_index, name="sitemap_index"),
    path("sitemap-static.xml", views.sitemap_static, name="sitemap_static"),
    path("sitemap-judges.xml", views.sitemap_judges, name="sitemap_judges"),
    path("sitemap-tags.xml", views.sitemap_tags, name="sitemap_tags"),
    path("sitemap-statutes.xml", views.sitemap_statutes, name="sitemap_statutes"),
    path("sitemap-opinions-<int:chunk>.xml", views.sitemap_opinions, name="sitemap_opinions"),
]
