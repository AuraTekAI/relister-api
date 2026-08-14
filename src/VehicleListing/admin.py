from django.contrib import admin

# Register your models here
from .models import VehicleListing, ListingUrl, Vehicle
from .models import FacebookListing, FacebookUserCredentials,GumtreeProfileListing,FacebookProfileListing, RelistingFacebooklisting,Invoice,CustomDomainProfileListing,FacebookListingSnapshot,UnpublishedListingSnapshot,ExtensionSyncStatus,HostedImage,VehicleListingImage
from .utils import reactivate_listing

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
    list_display = ('id','user', 'year', 'make', 'model', 'status', 'list_id','seller_profile_id','rate','is_relist','is_changed','has_images','sales','sold_at','listed_on','retry_count', 'created_at', 'updated_at')
    search_fields = ('user__email', 'year', 'make', 'model','status','list_id','seller_profile_id', 'vehicle__year', 'vehicle__make', 'vehicle__model')
    list_filter = ('user','status', 'is_relist', 'is_changed', 'has_images', 'sales',)
    actions = ['reactivate_sold_listings']

    def get_search_results(self, request, queryset, search_term):
        """
        Override search to handle numeric vehicle_id searches
        Allows searching by vehicle ID (e.g., "1012", "1153")
        """
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)

        # If search term is numeric, also search by vehicle_id
        if search_term.isdigit():
            queryset = queryset | VehicleListing.objects.filter(vehicle_id=int(search_term))
            use_distinct = True

        return queryset, use_distinct

    @admin.action(description="Reactivate selected listings (undo sold — dealer confirmed still for sale)")
    def reactivate_sold_listings(self, request, queryset):
        count = 0
        for listing in queryset.filter(status="sold"):
            reactivate_listing(listing)
            count += 1
        self.message_user(request, f"Reactivated {count} listing(s).")

class VehicleAdmin(admin.ModelAdmin):
    list_display = ('id', 'year', 'make', 'model', 'body_type', 'fuel_type', 'transmission', 'mileage', 'color', 'vin', 'created_at', 'updated_at')
    search_fields = ('year', 'make', 'model', 'vin', 'color')
    list_filter = ('year', 'body_type', 'fuel_type', 'transmission')
    readonly_fields = ('created_at', 'updated_at')

class GumtreeProfileListingAdmin(admin.ModelAdmin):
    list_display = ('user', 'url', 'status', 'profile_id', 'total_listings', 'processed_listings', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url')
    list_filter = ('user','status')

class CustomDomainProfileListingAdmin(admin.ModelAdmin):
    list_display = ('user', 'url', 'domain', 'status', 'profile_id', 'total_listings', 'processed_listings', 'created_at', 'updated_at')
    search_fields = ('user__email', 'url', 'domain')
    list_filter = ('user','status', 'domain')

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
    list_display = ('invoice_number', 'invoice_id', 'user', 'plan_name', 'total_amount', 'status', 'created_at')
    search_fields = ('invoice_number', 'invoice_id', 'user__email', 'stripe_invoice_id')
    list_filter = ('status', 'created_at')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)

admin.site.register(Invoice, InvoiceAdmin)
admin.site.register(VehicleListing, VehicleListingAdmin)
admin.site.register(Vehicle, VehicleAdmin)
admin.site.register(ListingUrl, ListingUrlAdmin)
admin.site.register(FacebookListing, FacebookListingAdmin)
admin.site.register(FacebookUserCredentials, FacebookUserCredentialsAdmin)
admin.site.register(GumtreeProfileListing, GumtreeProfileListingAdmin)
admin.site.register(CustomDomainProfileListing, CustomDomainProfileListingAdmin)
admin.site.register(FacebookProfileListing, FacebookProfileListingAdmin)
admin.site.register(RelistingFacebooklisting,RelistingFacebooklistingAdmin)


class FacebookListingSnapshotAdmin(admin.ModelAdmin):
    list_display = ('user', 'fb_listing_id', 'title', 'price', 'fb_published_at', 'days_on_facebook', 'is_aged', 'is_duplicate', 'duplicate_count', 'matched_listing', 'mode', 'synced_at')
    search_fields = ('user__email', 'fb_listing_id', 'title')
    list_filter = ('user', 'mode', 'is_aged', 'is_duplicate')

admin.site.register(FacebookListingSnapshot, FacebookListingSnapshotAdmin)


class UnpublishedListingSnapshotAdmin(admin.ModelAdmin):
    list_display = ('user', 'listing', 'title', 'price', 'images_count', 'reason', 'reason_detail', 'mode', 'synced_at')
    search_fields = ('user__email', 'title')
    list_filter = ('user', 'mode', 'reason')

admin.site.register(UnpublishedListingSnapshot, UnpublishedListingSnapshotAdmin)


class ExtensionSyncStatusAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'status_detail', 'fb_count', 'unpublished_count', 'mode', 'extension_version', 'synced_at')
    search_fields = ('user__email',)
    list_filter = ('status', 'mode')

admin.site.register(ExtensionSyncStatus, ExtensionSyncStatusAdmin)


class HostedImageAdmin(admin.ModelAdmin):
    list_display = ('id', 'content_hash', 'status', 'width', 'height', 'file_size_bytes', 'created_at')
    search_fields = ('content_hash', 'source_url')
    list_filter = ('status',)
    readonly_fields = ('content_hash', 'thumbnail_image', 'medium_image', 'large_image', 'created_at', 'updated_at')

admin.site.register(HostedImage, HostedImageAdmin)


class VehicleListingImageAdmin(admin.ModelAdmin):
    list_display = ('id', 'listing_id', 'position', 'status', 'retry_count', 'hosted_image', 'created_at', 'updated_at')
    search_fields = ('listing__id', 'source_url')
    list_filter = ('status',)
    raw_id_fields = ('listing', 'hosted_image')

admin.site.register(VehicleListingImage, VehicleListingImageAdmin)
