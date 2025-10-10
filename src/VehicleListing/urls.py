from django.urls import path
from .views import import_url_from_gumtree, all_vehicle_listing, VehicleListingViewSet, ListingUrlViewSet, FacebookUserCredentialsViewSet, get_gumtree_profile_listings, facebook_profile_listings,GumtreeProfileListingViewSet,FacebookProfileListingViewSet,get_montly_listings_report,get_facebook_session_status,get_user_gumtree_profile_vehicle_listings

urlpatterns = [
    path('import/', import_url_from_gumtree, name='import_url_from_gumtree'),
    path('gumtree/', all_vehicle_listing, name='all_vehicle_listing'),
    path('listing_urls/', ListingUrlViewSet.as_view({'get': 'list' , 'post': 'create'}), name='listing_url'),
    path('listing_urls/<int:pk>/', ListingUrlViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='listing_url_detail'),
    path('', VehicleListingViewSet.as_view({'get': 'list' , 'post': 'create'}), name='vehicle_listing'),
    path('<int:pk>/', VehicleListingViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='vehicle_listing_detail'),
    path('facebook_user/', FacebookUserCredentialsViewSet.as_view({'get': 'list' , 'post': 'create'}), name='facebook_user'),
    path('facebook_user/<int:pk>/', FacebookUserCredentialsViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='facebook_user_detail'),
    path('gumtree_profile_listings/', get_gumtree_profile_listings, name='get_gumtree_profile_listings'),
    path('facebook_profile_listings/', facebook_profile_listings, name='facebook_profile_listings'),
    path('gumtree_profile_listings_details/', GumtreeProfileListingViewSet.as_view({'get': 'list' , 'post': 'create'}), name='gumtree_profile_listings'),
    path('gumtree_profile_listings_details/<int:pk>/', GumtreeProfileListingViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='gumtree_profile_listings_detail'),
    path('facebook_profile_listings_details/', FacebookProfileListingViewSet.as_view({'get': 'list' , 'post': 'create'}), name='facebook_profile_listings'),
    path('facebook_profile_listings_details/<int:pk>/', FacebookProfileListingViewSet.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='facebook_profile_listings_detail'),
    path('monthly_report/', get_montly_listings_report, name='get_montly_listings_report'),
    path('facebook_session_status/', get_facebook_session_status, name='get_facebook_session_status'),
    path('gumtree-listings/', get_user_gumtree_profile_vehicle_listings, name='get_user_gumtree_profile_vehicle_listings'),
]
