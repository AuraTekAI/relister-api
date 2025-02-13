from django.contrib import admin

# Register your models here
from .models import VehicleListing, ListingUrl
from .models import FacebookListing, FacebookUserCredentials,GumtreeProfileListing

class FacebookListingAdmin(admin.ModelAdmin):
    list_display = ('user', 'listing', 'status', 'error_message', 'created_at', 'updated_at')
    search_fields = ('user__email', 'listing__title')
    list_filter = ('user',)

class FacebookUserCredentialsAdmin(admin.ModelAdmin):
    list_display = ('user', 'email','session_cookie', 'created_at', 'updated_at')
    search_fields = ('user__email', 'email')
    list_filter = ('user',)

class ListingUrlAdmin(admin.ModelAdmin):
    list_display = ('user', 'url', 'status', 'error_message', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url')
    list_filter = ('user',)

class VehicleListingAdmin(admin.ModelAdmin):
    list_display = ('id','user', 'year', 'make', 'model', 'status', 'gumtree_profile_id','created_at', 'updated_at')
    search_fields = ('user__email', 'year', 'make', 'model')
    list_filter = ('user',)

class GumtreeProfileListingAdmin(admin.ModelAdmin): 
    list_display = ('user', 'url', 'status', 'error_message', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url')
    list_filter = ('user',)

admin.site.register(VehicleListing, VehicleListingAdmin)
admin.site.register(ListingUrl, ListingUrlAdmin)
admin.site.register(FacebookListing, FacebookListingAdmin)
admin.site.register(FacebookUserCredentials, FacebookUserCredentialsAdmin)
admin.site.register(GumtreeProfileListing, GumtreeProfileListingAdmin)