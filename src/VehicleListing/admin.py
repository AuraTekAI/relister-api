from django.contrib import admin

# Register your models here
from .models import VehicleListing, ListingUrl
from .models import FacebookListing, FacebookUserCredentials,GumtreeProfileListing,FacebookProfileListing

class FacebookListingAdmin(admin.ModelAdmin):
    list_display = ('user', 'listing', 'status', 'error_message', 'created_at', 'updated_at')
    search_fields = ('user__email', 'listing__title')
    list_filter = ('user',)

class FacebookUserCredentialsAdmin(admin.ModelAdmin):
    list_display = ('user','session_cookie', 'created_at', 'updated_at')
    search_fields = ('user__email',)
    list_filter = ('user',)

class ListingUrlAdmin(admin.ModelAdmin):
    list_display = ('user', 'url', 'status', 'error_message', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url')
    list_filter = ('user',)

class VehicleListingAdmin(admin.ModelAdmin):
    list_display = ('id','user', 'year', 'make', 'model', 'status', 'seller_profile_id','renew_date','created_at', 'updated_at')
    search_fields = ('user__email', 'year', 'make', 'model')
    list_filter = ('user',)

class GumtreeProfileListingAdmin(admin.ModelAdmin): 
    list_display = ('user', 'url', 'status', 'profile_id', 'total_listings', 'processed_listings', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url')
    list_filter = ('user',)

class FacebookProfileListingAdmin(admin.ModelAdmin):        
    list_display = ('user', 'url', 'status', 'profile_id', 'total_listings', 'processed_listings', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url')
    list_filter = ('user',)

admin.site.register(VehicleListing, VehicleListingAdmin)
admin.site.register(ListingUrl, ListingUrlAdmin)
admin.site.register(FacebookListing, FacebookListingAdmin)
admin.site.register(FacebookUserCredentials, FacebookUserCredentialsAdmin)
admin.site.register(GumtreeProfileListing, GumtreeProfileListingAdmin)
admin.site.register(FacebookProfileListing, FacebookProfileListingAdmin)