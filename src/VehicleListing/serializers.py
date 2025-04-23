from rest_framework import serializers
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookProfileListing, GumtreeProfileListing, RelistingFacebooklisting

class VehicleListingSerializer(serializers.ModelSerializer):
    relisting_dates = serializers.SerializerMethodField()

    class Meta:
        model = VehicleListing
        fields = '__all__'
        ordering = ['-updated_at']

    def get_relisting_dates(self, obj):
        # Retrieve all relisting dates for the given vehicle listing
        relisting_dates = RelistingFacebooklisting.objects.filter(listing=obj).values_list('relisting_date', flat=True)
        return list(relisting_dates)
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