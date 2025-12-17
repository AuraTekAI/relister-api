from django.contrib import admin

# Register your models here
from .models import VehicleListing, ListingUrl
from .models import FacebookListing, FacebookUserCredentials,GumtreeProfileListing,FacebookProfileListing, RelistingFacebooklisting,Invoice

class FacebookListingAdmin(admin.ModelAdmin):
    list_display = ('user', 'listing', 'status', 'error_message', 'created_at', 'updated_at')
    search_fields = ('user__email', 'listing__title')
    list_filter = ('user',)

class FacebookUserCredentialsAdmin(admin.ModelAdmin):
    list_display = ('user','session_cookie','status','status_reminder', 'created_at', 'updated_at')
    search_fields = ('user__email',)
    list_filter = ('user',)

class ListingUrlAdmin(admin.ModelAdmin):
    list_display = ('user', 'url', 'listing_id', 'status', 'error_message', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url','listing_id')
    list_filter = ('user',)

class VehicleListingAdmin(admin.ModelAdmin):
    list_display = ('id','user', 'year', 'make', 'model', 'status', 'list_id','seller_profile_id','rate','is_relist','has_images','sales','listed_on','retry_count', 'created_at', 'updated_at')
    search_fields = ('user__email', 'year', 'make', 'model','status','list_id','seller_profile_id')
    list_filter = ('user','status', 'is_relist', 'has_images', 'sales',)

class GumtreeProfileListingAdmin(admin.ModelAdmin): 
    list_display = ('user', 'url', 'status', 'profile_id', 'total_listings', 'processed_listings', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url')
    list_filter = ('user','status')

class FacebookProfileListingAdmin(admin.ModelAdmin):        
    list_display = ('user', 'url', 'status', 'profile_id', 'total_listings', 'processed_listings', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url')
    list_filter = ('user','status')

class RelistingFacebooklistingAdmin(admin.ModelAdmin):
    list_display = ("user","listing","relisting_date","status","last_relisting_status","created_at","updated_at")
    search_fields = ('user__email',"listing__year","listing__make","listing__model",)
    list_filter = ('user',"listing__status",)
    ordering = ('-relisting_date',)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_id", "user", "total_amount", "created_at", "updated_at")
    search_fields = ("invoice_id", "user__email")
    list_filter = ("created_at", "updated_at")
    readonly_fields = ("invoice_id", "created_at", "updated_at")
    ordering = ("-created_at",)

admin.site.register(Invoice, InvoiceAdmin)
admin.site.register(VehicleListing, VehicleListingAdmin)
admin.site.register(ListingUrl, ListingUrlAdmin)
admin.site.register(FacebookListing, FacebookListingAdmin)
admin.site.register(FacebookUserCredentials, FacebookUserCredentialsAdmin)
admin.site.register(GumtreeProfileListing, GumtreeProfileListingAdmin)
admin.site.register(FacebookProfileListing, FacebookProfileListingAdmin)
admin.site.register(RelistingFacebooklisting,RelistingFacebooklistingAdmin)
