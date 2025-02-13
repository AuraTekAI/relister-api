from django.urls import path
from .views import import_url_from_gumtree, all_vehicle_listing, VehicleListingViewSet, ListingUrlViewSet, FacebookUserCredentialsViewSet, get_facebook_profile_listings , get_gumtree_profile_listings

urlpatterns = [
    path('import/', import_url_from_gumtree, name='import_url_from_gumtree'),
    path('gumtree/', all_vehicle_listing, name='all_vehicle_listing'),
    path('listing_urls/', ListingUrlViewSet.as_view({'get': 'list' , 'post': 'create'}), name='listing_url'),
    path('listing_urls/<int:pk>/', ListingUrlViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='listing_url_detail'),
    path('', VehicleListingViewSet.as_view({'get': 'list' , 'post': 'create'}), name='vehicle_listing'),
    path('<int:pk>/', VehicleListingViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='vehicle_listing_detail'),
    path('facebook_user/', FacebookUserCredentialsViewSet.as_view({'get': 'list' , 'post': 'create'}), name='facebook_user'),
    path('facebook_user/<int:pk>/', FacebookUserCredentialsViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='facebook_user_detail'),
    path('facebook_profile_listings/', get_facebook_profile_listings, name='get_facebook_profile_listings'),
    path('facebook_profile_listings/<int:pk>/', get_facebook_profile_listings, name='get_facebook_profile_listings'),
    path('gumtree_profile_listings/', get_gumtree_profile_listings, name='get_gumtree_profile_listings'),
    path('gumtree_profile_listings/<int:pk>/', get_gumtree_profile_listings, name='get_gumtree_profile_listings_detail'),
]
