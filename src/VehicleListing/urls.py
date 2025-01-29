from django.urls import path
from .views import import_url_from_gumtree, all_vehicle_listing, all_urls, VehicleListingViewSet, ListingUrlViewSet

urlpatterns = [
    path('import/', import_url_from_gumtree, name='import_url_from_gumtree'),
    path('gumtree/', all_vehicle_listing, name='all_vehicle_listing'),
    path('listing_urls/', ListingUrlViewSet.as_view({'get': 'list' , 'post': 'create'}), name='listing_url'),
    path('listing_urls/<int:pk>/', ListingUrlViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='listing_url_detail'),
    path('', VehicleListingViewSet.as_view({'get': 'list' , 'post': 'create'}), name='vehicle_listing'),
    path('<int:pk>/', VehicleListingViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='vehicle_listing_detail'),
]
