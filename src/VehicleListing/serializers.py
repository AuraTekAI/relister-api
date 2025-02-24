from rest_framework import serializers
from .models import VehicleListing, ListingUrl , FacebookUserCredentials,FacebookProfileListing,GumtreeProfileListing
class VehicleListingSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleListing
        fields = '__all__'
        ordering = ['-updated_at']
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