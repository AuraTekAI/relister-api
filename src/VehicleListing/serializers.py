from urllib.parse import quote

from django.urls import reverse
from rest_framework import serializers
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookProfileListing, GumtreeProfileListing, RelistingFacebooklisting, DNACarSalesProfileListing

DNA_IMAGE_URL_PREFIX = "https://www.dnacarsales.com.au/"


class VehicleListingSerializer(serializers.ModelSerializer):
    relisting_dates = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()

    class Meta:
        model = VehicleListing
        fields = '__all__'
        ordering = ['-updated_at']

    def get_relisting_dates(self, obj):
        # Retrieve all relisting dates for the given vehicle listing
        relisting_dates = RelistingFacebooklisting.objects.filter(listing=obj).values_list('relisting_date', flat=True)
        return list(relisting_dates)

    def get_images(self, obj):
        # DNA Car Sales' webserver returns no CORS headers, so the extension
        # cannot fetch its image URLs from the Facebook tab. Rewrite each DNA
        # URL to our own CORS-friendly proxy endpoint (mirrors what
        # images.gumtree.com.au does for Gumtree URLs). Non-DNA URLs and rows
        # with missing request context fall through unchanged so the Gumtree
        # path is not affected.
        urls = obj.images or []
        request = self.context.get('request')
        if not request:
            return list(urls)
        try:
            proxy_base = request.build_absolute_uri(reverse('dnacarsales_image_proxy'))
        except Exception:
            return list(urls)
        rewritten = []
        for url in urls:
            if url and url.startswith(DNA_IMAGE_URL_PREFIX):
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
class DNACarSalesProfileListingSerializer(serializers.ModelSerializer):
    class Meta:
        model = DNACarSalesProfileListing
        fields = '__all__'
        ordering = ['-updated_at']