from django.db import models
from accounts.models import User
# from django.conf import settings
# Create your models here.

class VehicleListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    list_id = models.CharField(max_length=255)
    year = models.CharField(max_length=255)
    body_type = models.CharField(max_length=255)
    fuel_type = models.CharField(max_length=255)
    color = models.CharField(max_length=255)
    variant = models.CharField(max_length=255)
    make = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    price = models.CharField(max_length=255)
    mileage = models.IntegerField()
    # category = models.CharField(max_length=255)
    description = models.TextField()
    images = models.URLField()  # Store image URLs as JSON
    location = models.CharField(max_length=255)
    url = models.URLField()
    status = models.CharField(max_length=255, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.year} {self.make} {self.model}"
    
    
class ListingUrl(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=255, null=True)
    error_message = models.TextField(null=True)
    def __str__(self):
        return f"{self.url}"


class FacebookListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    listing = models.ForeignKey(VehicleListing, on_delete=models.CASCADE)
    status = models.CharField(max_length=255, null=True)
    error_message = models.TextField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.listing.make} {self.listing.model}"