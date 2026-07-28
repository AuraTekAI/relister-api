from urllib.parse import quote

from django.urls import reverse
from rest_framework import serializers

from accounts.models import User

from .custom_domain_adapters import any_needs_image_proxy
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookProfileListing, GumtreeProfileListing, RelistingFacebooklisting, CustomDomainProfileListing, VehicleListingImage


def _rewrite_proxy_url(url, request):
    """Rewrite a raw dealer/Gumtree URL through custom_domain_image_proxy when
    its host needs it (no CORS headers of its own). Shared by the
    extension-facing serializers below and the storefront's raw-URL fallback."""
    if not url or not request:
        return url
    if not any_needs_image_proxy(url):
        return url
    try:
        proxy_base = request.build_absolute_uri(reverse('custom_domain_image_proxy'))
    except Exception:
        return url
    return f"{proxy_base}?url={quote(url, safe='')}"


def _resolve_storefront_images(listing, size, request):
    """
    Ordered images for the public storefront: prefer our own S3/CDN URL for
    whichever photos have finished processing, and fall back to the raw
    (proxied-if-needed) source URL for anything still pending/failed or for
    rows that predate this pipeline and have no image_slots yet at all — so
    the storefront never shows a broken image while the async pipeline (or a
    backfill) catches up.

    Returns a list of {'url': str, 'is_hosted': bool} — is_hosted is True only
    for images actually served from our own AWS (S3/CloudFront) copy; False
    means it's still the raw/proxied external source URL.
    """
    slots = list(listing.image_slots.select_related('hosted_image').order_by('position'))
    if not slots:
        return [
            {'url': url, 'is_hosted': False}
            for url in (_rewrite_proxy_url(u, request) for u in (listing.images or []))
            if url
        ]

    resolved = []
    for slot in slots:
        if slot.status == VehicleListingImage.STATUS_READY and slot.hosted_image_id:
            url = slot.hosted_image.url_for(size)
            is_hosted = True
        else:
            url = _rewrite_proxy_url(slot.source_url, request)
            is_hosted = False
        if url:
            resolved.append({'url': url, 'is_hosted': is_hosted})
    return resolved


# State-code → full-name mapping used when assembling a fallback `location`
# string for custom-domain listings from User.dealership_state. Kept local to
# this module so VehicleListing/utils.get_full_state_name (used by the Gumtree
# scrape path) stays untouched.
_AU_STATE_FULL_NAMES = {
    'WA': 'Western Australia',
    'NSW': 'New South Wales',
    'VIC': 'Victoria',
    'QLD': 'Queensland',
    'SA': 'South Australia',
    'TAS': 'Tasmania',
    'ACT': 'Australian Capital Territory',
    'NT': 'Northern Territory',
}


class VehicleListingSerializer(serializers.ModelSerializer):
    relisting_dates = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    # Override the model field so custom-domain rows missing a per-listing
    # location can fall back to the dealer's saved suburb/state (auto-discovered
    # from their custom_domain_url at signup). Gumtree rows always carry their
    # own `adLocationData`-derived location and bypass the fallback.
    location = serializers.SerializerMethodField()

    class Meta:
        model = VehicleListing
        fields = '__all__'
        ordering = ['-updated_at']

    def get_relisting_dates(self, obj):
        relisting_dates = RelistingFacebooklisting.objects.filter(listing=obj).values_list('relisting_date', flat=True)
        return list(relisting_dates)

    def get_location(self, obj):
        # Gumtree path is sacred — its `location` is set per-ad by the Gumtree
        # scraper from `adLocationData`. Pass through unchanged whenever the
        # row has a stored value, regardless of source.
        if obj.location:
            return obj.location
        # Only inject for genuine custom-domain rows. Belt-and-braces: require
        # custom_domain_profile to be set AND gumtree_profile to be unset, so a
        # row that somehow has both never accidentally picks up the fallback.
        if obj.custom_domain_profile_id is None or obj.gumtree_profile_id is not None:
            return obj.location
        request = self.context.get('request') if hasattr(self, 'context') else None
        user = getattr(request, 'user', None) if request else None
        if user is None or not getattr(user, 'is_authenticated', False):
            return obj.location
        suburb = getattr(user, 'dealership_suburb', None)
        state = getattr(user, 'dealership_state', None)
        if not suburb or not state:
            return obj.location
        full_state = _AU_STATE_FULL_NAMES.get(state, state)
        return f"{suburb}, {full_state}"

    def get_images(self, obj):
        # Custom-domain sites typically don't return CORS headers, so the
        # extension cannot fetch their image URLs from the Facebook tab.
        # Rewrite each image whose adapter declares needs_image_proxy() to
        # our own CORS-friendly proxy (mirrors what images.gumtree.com.au
        # does for Gumtree URLs). Intentionally still the RAW/proxied source
        # URL, not our S3-hosted copy — the extension re-uploads these bytes
        # straight to Facebook and doesn't go through the storefront.
        request = self.context.get('request')
        return [_rewrite_proxy_url(url, request) for url in (obj.images or [])]
class ListingUrlSerializer(serializers.ModelSerializer):
    class Meta:
        model = ListingUrl
        fields = '__all__'
        ordering = ['-updated_at']
class FacebookUserCredentialsSerializer(serializers.ModelSerializer):
    class Meta:
        model = FacebookUserCredentials
        fields = '__all__'
        ordering = ['-updated_at']
class FacebookProfileListingSerializer(serializers.ModelSerializer):
    class Meta:
        model = FacebookProfileListing
        fields = '__all__'
        ordering = ['-updated_at']
class GumtreeProfileListingSerializer(serializers.ModelSerializer):
    class Meta:
        model = GumtreeProfileListing
        fields = '__all__'
        ordering = ['-updated_at']
class CustomDomainProfileListingSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomDomainProfileListing
        fields = '__all__'
        ordering = ['-updated_at']


class CustomDomainVehicleListingSerializer(VehicleListingSerializer):
    has_images = serializers.SerializerMethodField()

    def get_has_images(self, obj):
        return bool(obj.images)


class ProductListSerializer(serializers.ModelSerializer):
    """Lightweight, public-facing shape for storefront product grids/cards."""
    name = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()

    class Meta:
        model = VehicleListing
        fields = [
            'id', 'name', 'image', 'price',
            'year', 'body_type', 'fuel_type', 'variant', 'make', 'model',
            'mileage', 'transmission', 'color',
            'description', 'location', 'total_view_count',
        ]

    def get_name(self, obj):
        return ' '.join(str(part) for part in [obj.year, obj.make, obj.model] if part)

    def get_image(self, obj):
        images = _resolve_storefront_images(obj, 'medium', self.context.get('request'))
        return images[0]['url'] if images else None


class ProductDetailSerializer(serializers.ModelSerializer):
    """Public single-product detail shape, keyed by the slug lookup endpoint."""
    name = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    dealer_phone = serializers.SerializerMethodField()

    class Meta:
        model = VehicleListing
        fields = [
            'id', 'name', 'images', 'price',
            'year', 'body_type', 'fuel_type', 'variant', 'make', 'model',
            'description', 'location', 'condition', 'transmission',
            'mileage', 'exterior_colour', 'interior_colour', 'dealer_phone',
        ]

    def get_name(self, obj):
        return ' '.join(str(part) for part in [obj.year, obj.make, obj.model] if part)

    def get_dealer_phone(self, obj):
        return getattr(obj.user, 'phone_number', None)

    def get_images(self, obj):
        return _resolve_storefront_images(obj, 'large', self.context.get('request'))


class DealerListSerializer(serializers.ModelSerializer):
    """Public-facing dealer/seller directory entry for the storefront."""
    name = serializers.SerializerMethodField()
    address = serializers.SerializerMethodField()
    active_listing_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'name', 'address',
            'dealership_suburb', 'dealership_state', 'phone_number',
            'active_listing_count',
        ]

    def get_name(self, obj):
        return obj.dealership_name or obj.contact_person_name or obj.email

    def get_address(self, obj):
        parts = [obj.dealership_suburb, obj.dealership_state]
        return ', '.join(part for part in parts if part) or None
