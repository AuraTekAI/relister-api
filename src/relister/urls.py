
from django.contrib import admin
from django.urls import path, include
from django.conf import settings

from rest_framework import permissions

from drf_yasg.views import get_schema_view
from drf_yasg import openapi

schema_view = get_schema_view(
   openapi.Info(
      title="relister API",
      default_version='v1',
      description="powered by relister",
      terms_of_service="https://www.ourapp.com/policies/terms/", # TODO: Add THIS
      contact=openapi.Contact(email="info@relister.net"),
      license=openapi.License(name="License"),), # TODO: Add THIS
   public=True,
   permission_classes=[permissions.AllowAny],
)


urlpatterns = [
    # Admin routes
    path("admin/", admin.site.urls),
    path('api/', include('accounts.urls')),


]
if settings.ENVIRONMENT == 'DEVELOPMENT' or settings.ENVIRONMENT == 'LOCAL':
    urlpatterns.append(path ('', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),)
    urlpatterns.append(path ('api/api.json', schema_view.without_ui( cache_timeout=0), name='schema-swagger-ui'),)
    urlpatterns.append(path ('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),)




admin.site.site_header = 'relister'
admin.site.site_title = 'relister'
