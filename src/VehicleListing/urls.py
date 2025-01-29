from django.urls import path
from .views import import_url_from_gumtree, all_vehicle_listing, all_urls

urlpatterns = [
    path('', import_url_from_gumtree, name='import_url_from_gumtree'),
    path('gumtree/', all_vehicle_listing, name='all_vehicle_listing'),
    path('all-urls/', all_urls, name='all_urls'),
]
