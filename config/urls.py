from django.contrib import admin
from django.http import HttpResponse
from django.urls import path
from django.views.generic import RedirectView

admin.site.site_header = "Gestão de Alojamento"
admin.site.site_title = "Gestão de Alojamento"
admin.site.index_title = "Painel"

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("healthz", lambda request: HttpResponse("ok", content_type="text/plain")),
    path("admin/", admin.site.urls),
]
