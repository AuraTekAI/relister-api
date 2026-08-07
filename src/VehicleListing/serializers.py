from urllib.parse import quote

from django.conf import settings
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


def _resolve_storefront_images(listing, size, request, require_hosted=False):
    """
    Ordered images for the public storefront: prefer our own S3/CDN URL for
    whichever photos have finished processing.

    require_hosted=False (default, used by the extension's publish flow):
    falls back to the raw (proxied-if-needed) source URL for anything still
    pending/failed or for rows that predate this pipeline and have no
    image_slots yet at all — so publishing to Facebook always has *something*
    to upload while the async pipeline (or a backfill) catches up.

    require_hosted=True (used by the public storefront — see ProductListSerializer
    /ProductDetailSerializer): only our own S3/CDN copy is ever returned — a
    photo that isn't hosted yet is dropped rather than falling back to the
    external Gumtree/dealer URL, so the storefront never depends on Gumtree
    image hosting.

    Returns a list of {'url': str, 'is_hosted': bool} — is_hosted is True only
    for images actually served from our own AWS (S3/CloudFront) copy; False
    means it's still the raw/proxied external source URL.
    """
    slots = list(listing.image_slots.select_related('hosted_image').order_by('position'))
    if not slots:
        if require_hosted:
            return []
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
        elif require_hosted:
            continue
        else:
            url = _rewrite_proxy_url(slot.source_url, request)
            is_hosted = False
        if url:
            resolved.append({'url': url, 'is_hosted': is_hosted})
    return resolved


def _resolve_gumtree_hosted_only_images(listing):
    """Gumtree's extension-facing image list: prefer our own S3-hosted FB-safe
    JPEG for whichever photos have finished processing, and fall back to the
    raw Gumtree URL for anything still pending/processing/failed, or for a
    legacy/just-scraped listing with no image_slots yet at all.

    Gumtree's CDN is CORS-friendly (see any_needs_image_proxy), so the raw URL
    needs no proxying and is always safe to hand straight to the extension —
    that's what lets this fall back per-photo instead of withholding the whole
    listing. This is what the extension's home page reads to show images the
    moment a listing is scraped, well before the async S3 hosting pipeline (or
    even the first sync_listing_images call) has run: showing nothing until
    every photo is S3-hosted made the home page display no images at all for
    any listing with even one photo still processing. Facebook publishing is
    unaffected by this fallback — it still gets the S3 copy for every photo
    that's actually finished; only photos that aren't hosted yet use the raw
    URL, exactly like every other resolver in this module already does."""
    slots = list(listing.image_slots.select_related('hosted_image').order_by('position'))
    if not slots:
        # Legacy/just-scraped listing with no slots yet at all — show the raw
        # Gumtree URLs immediately; sync_listing_images creates and processes
        # slots in the background without blocking display.
        return [url for url in (listing.images or []) if url]

    urls = []
    for slot in slots:
        url = None
        if slot.status == VehicleListingImage.STATUS_READY and slot.hosted_image_id:
            hosted = slot.hosted_image.upload_url()
            if hosted and hosted.lower().split('?')[0].endswith(('.jpg', '.jpeg', '.png')):
                url = hosted
        if not url:
            url = slot.source_url
        if url:
            urls.append(url)
    return urls


def _resolve_extension_images(listing, request):
    """Ordered image URLs for the Chrome extension to re-upload to Facebook.

    Gumtree listings are routed to _resolve_gumtree_hosted_only_images (see
    its docstring) — S3 is preferred per-photo the moment it's hosted, with
    the raw Gumtree URL as the fallback for anything not hosted yet.

    For every other source (custom-domain), prefer our own S3/CloudFront copy
    (the FB-safe JPEG upload variant) for every photo that's finished
    processing, and fall back to the raw source URL (routed through
    custom_domain_image_proxy only for hosts that need it) for photos still
    pending/failed, or for legacy rows with no image_slots yet — the per-slot
    fallback is what keeps publishing safe (never blocked) for anything that
    hasn't finished hosting yet.

    Why this exists: the previous behaviour proxied EVERY full-size original on
    every publish. For custom-domain dealers (whose images all need the proxy,
    unlike Gumtree) that meant the server live-fetched and buffered N large
    originals from the dealer CDN at once per publish, saturating gunicorn
    workers — a subset timed out, the extension dropped those photos and tripped
    its PARTIAL_IMAGE_UPLOAD guard. Serving the pre-hosted CDN copy keeps the
    proxy off the hot path for the common case.

    Custom-domain's rollout of this is gated by settings.EXTENSION_USE_HOSTED_IMAGES
    (staged: default False until the migration + backfill_upload_variants have
    run, then flipped True via env alone — no deploy) so it can be switched off
    instantly if Facebook ever rejects the hosted JPEG variant."""
    is_gumtree = bool(getattr(listing, 'gumtree_profile_id', None)
                      or getattr(listing, 'gumtree_url_id', None))

    if is_gumtree:
        return _resolve_gumtree_hosted_only_images(listing)

    if not getattr(settings, 'EXTENSION_USE_HOSTED_IMAGES', True):
        return [
            url for url in (_rewrite_proxy_url(u, request) for u in (listing.images or []))
            if url
        ]

    slots = list(listing.image_slots.select_related('hosted_image').order_by('position'))
    if not slots:
        return [
            url for url in (_rewrite_proxy_url(u, request) for u in (listing.images or []))
            if url
        ]

    urls = []
    for slot in slots:
        url = None
        if slot.status == VehicleListingImage.STATUS_READY and slot.hosted_image_id:
            # Serve the FB-safe JPEG upload variant. Facebook Marketplace only
            # accepts JPEG/PNG for listing photos (our storefront WebP variants
            # are rejected), so we deliberately DON'T use url_for('large').
            # Images processed before this variant existed have no upload copy
            # yet (upload_url() -> None) and fall through to the proxy until the
            # backfill runs — belt-and-braces format check keeps that safe.
            hosted = slot.hosted_image.upload_url()
            if hosted and hosted.lower().split('?')[0].endswith(('.jpg', '.jpeg', '.png')):
                url = hosted
        if not url:
            url = _rewrite_proxy_url(slot.source_url, request)
        if url:
            urls.append(url)
    return urls


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
        # extension cannot fetch their raw image URLs from the Facebook tab.
        # Prefer our own S3/CloudFront copy (CORS-friendly, resized, CDN-cached)
        # for photos that have finished processing, and fall back to the
        # CORS-friendly proxy for anything still pending. Serving the pre-hosted
        # copy keeps the worker-pinning proxy off the hot path and fixes the
        # PARTIAL_IMAGE_UPLOAD drops custom-domain dealers hit when every
        # full-size original was proxied at publish time. See
        # _resolve_extension_images for the rationale + the env kill-switch.
        request = self.context.get('request')
        return _resolve_extension_images(obj, request)
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
        images = _resolve_storefront_images(obj, 'medium', self.context.get('request'), require_hosted=True)
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
        return _resolve_storefront_images(obj, 'large', self.context.get('request'), require_hosted=True)


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
