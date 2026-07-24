from urllib.parse import quote

from django.urls import reverse
from rest_framework import serializers

from accounts.models import User

from .custom_domain_adapters import any_needs_image_proxy
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookProfileListing, GumtreeProfileListing, RelistingFacebooklisting, CustomDomainProfileListing


def _proxied_image_urls(urls, request):
    # Custom-domain sites typically don't return CORS headers, so the
    # extension cannot fetch their image URLs from the Facebook tab.
    # Rewrite each image whose adapter declares needs_image_proxy() to
    # our own CORS-friendly proxy (mirrors what images.gumtree.com.au
    # does for Gumtree URLs).
    if not request:
        return list(urls)
    try:
        proxy_base = request.build_absolute_uri(reverse('custom_domain_image_proxy'))
    except Exception:
        return list(urls)
    rewritten = []
    for url in urls:
        if url and any_needs_image_proxy(url):
            rewritten.append(f"{proxy_base}?url={quote(url, safe='')}")
        else:
            rewritten.append(url)
    return rewritten


class VehicleListingSerializer(serializers.ModelSerializer):
    relisting_dates = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    vin = serializers.CharField(source='vehicle.vin', read_only=True)
    make = serializers.CharField(source='vehicle.make', read_only=True)
    model = serializers.CharField(source='vehicle.model', read_only=True)
    year = serializers.CharField(source='vehicle.year', read_only=True)
    mileage = serializers.IntegerField(source='vehicle.mileage', read_only=True)
    transmission = serializers.CharField(source='vehicle.transmission', read_only=True)
    fuel_type = serializers.CharField(source='vehicle.fuel_type', read_only=True)
    body_type = serializers.CharField(source='vehicle.body_type', read_only=True)
    color = serializers.CharField(source='vehicle.color', read_only=True)

    class Meta:
        model = VehicleListing
        fields = '__all__'
        ordering = ['-updated_at']

    def get_relisting_dates(self, obj):
        relisting_dates = RelistingFacebooklisting.objects.filter(listing=obj).values_list('relisting_date', flat=True)
        return list(relisting_dates)

    def get_images(self, obj):
        urls = obj.vehicle.images.values_list('image_url', flat=True)
        return _proxied_image_urls(urls, self.context.get('request'))
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
        return obj.vehicle.images.exists()


class ProductListSerializer(serializers.ModelSerializer):
    """Lightweight, public-facing shape for storefront product grids/cards."""
    name = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    year = serializers.CharField(source='vehicle.year', read_only=True)
    body_type = serializers.CharField(source='vehicle.body_type', read_only=True)
    fuel_type = serializers.CharField(source='vehicle.fuel_type', read_only=True)
    make = serializers.CharField(source='vehicle.make', read_only=True)
    model = serializers.CharField(source='vehicle.model', read_only=True)
    mileage = serializers.IntegerField(source='vehicle.mileage', read_only=True)
    transmission = serializers.CharField(source='vehicle.transmission', read_only=True)
    color = serializers.CharField(source='vehicle.color', read_only=True)

    class Meta:
        model = VehicleListing
        fields = [
            'id', 'name', 'image', 'price',
            'year', 'body_type', 'fuel_type', 'make', 'model',
            'mileage', 'transmission', 'color',
            'description',
        ]

    def get_name(self, obj):
        return ' '.join(str(part) for part in [obj.vehicle.year, obj.vehicle.make, obj.vehicle.model] if part)

    def get_image(self, obj):
        image = obj.vehicle.images.first()
        return image.image_url if image else None


class ProductDetailSerializer(serializers.ModelSerializer):
    """Public single-product detail shape, keyed by the slug lookup endpoint."""
    name = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    dealer_phone = serializers.SerializerMethodField()
    year = serializers.CharField(source='vehicle.year', read_only=True)
    body_type = serializers.CharField(source='vehicle.body_type', read_only=True)
    fuel_type = serializers.CharField(source='vehicle.fuel_type', read_only=True)
    make = serializers.CharField(source='vehicle.make', read_only=True)
    model = serializers.CharField(source='vehicle.model', read_only=True)
    transmission = serializers.CharField(source='vehicle.transmission', read_only=True)
    mileage = serializers.IntegerField(source='vehicle.mileage', read_only=True)

    class Meta:
        model = VehicleListing
        fields = [
            'id', 'name', 'images', 'price',
            'year', 'body_type', 'fuel_type', 'make', 'model',
            'description', 'transmission',
            'mileage', 'dealer_phone',
        ]

    def get_name(self, obj):
        return ' '.join(str(part) for part in [obj.vehicle.year, obj.vehicle.make, obj.vehicle.model] if part)

    def get_dealer_phone(self, obj):
        return getattr(obj.user, 'phone_number', None)

    def get_images(self, obj):
        urls = obj.vehicle.images.values_list('image_url', flat=True)
        return _proxied_image_urls(urls, self.context.get('request'))


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
