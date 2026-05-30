"""URL routes for the opinions app."""
from django.urls import path

from opinions import views

app_name = "opinions"

urlpatterns = [
    path("", views.home, name="home"),
]
