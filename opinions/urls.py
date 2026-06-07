"""URL routes for the opinions app."""
from django.urls import path

from opinions import views

app_name = "opinions"

urlpatterns = [
    path("", views.home, name="home"),
    path("opinions/", views.opinion_list, name="opinion_list"),
    path("about/", views.about, name="about"),
    path("privacy/", views.privacy, name="privacy"),
    path("support/", views.support, name="support"),
    path("request-state/", views.request_state, name="request_state"),
    path("request-state/thanks/", views.request_state_thanks, name="request_state_thanks"),
    path("opinion/<str:case_number>/", views.opinion_detail, name="detail"),
    path("current-judges/", views.current_judges, name="current_judges"),
    path("judge/<slug:slug>/", views.judge_detail, name="judge_detail"),
    path("tag/", views.tag_index, name="tag_index"),
    path("tag/<slug:slug>/", views.tag_detail, name="tag_detail"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("llms.txt", views.llms_txt, name="llms_txt"),
    path("sitemap.xml", views.sitemap_index, name="sitemap_index"),
    path("sitemap-static.xml", views.sitemap_static, name="sitemap_static"),
    path("sitemap-judges.xml", views.sitemap_judges, name="sitemap_judges"),
    path("sitemap-opinions-<int:chunk>.xml", views.sitemap_opinions, name="sitemap_opinions"),
]
