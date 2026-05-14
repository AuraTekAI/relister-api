from urllib.parse import quote

from django.urls import reverse
from rest_framework import serializers

from .custom_domain_adapters import any_needs_image_proxy
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookProfileListing, GumtreeProfileListing, RelistingFacebooklisting, CustomDomainProfileListing


class VehicleListingSerializer(serializers.ModelSerializer):
    relisting_dates = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()

    class Meta:
        model = VehicleListing
        fields = '__all__'
        ordering = ['-updated_at']

    def get_relisting_dates(self, obj):
        relisting_dates = RelistingFacebooklisting.objects.filter(listing=obj).values_list('relisting_date', flat=True)
        return list(relisting_dates)

    def get_images(self, obj):
        # Custom-domain sites typically don't return CORS headers, so the
        # extension cannot fetch their image URLs from the Facebook tab.
        # Rewrite each image whose adapter declares needs_image_proxy() to
        # our own CORS-friendly proxy (mirrors what images.gumtree.com.au
        # does for Gumtree URLs).
        urls = obj.images or []
        request = self.context.get('request')
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
