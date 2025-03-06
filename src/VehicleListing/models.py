from django.db import models
from accounts.models import User

class ListingUrl(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=255, null=True)
    error_message = models.TextField(null=True)
    def __str__(self):
        return f"{self.url}"
    
class FacebookUserCredentials(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    username = models.CharField(max_length=255,null=True,blank=True,unique=True)
    password = models.CharField(max_length=255)
    session_cookie = models.JSONField(null=True,blank=True,default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class GumtreeProfileListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField(null=True,blank=True)
    total_listings = models.IntegerField(null=True,blank=True)
    processed_listings = models.IntegerField(null=True,blank=True)
    status = models.CharField(max_length=255, null=True,blank=True)
    profile_id = models.CharField(max_length=255, null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.url}"

class FacebookProfileListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField(null=True,blank=True)
    total_listings = models.IntegerField(null=True,blank=True)
    processed_listings = models.IntegerField(null=True,blank=True)
    status = models.CharField(max_length=255, null=True)
    profile_id = models.CharField(max_length=255, null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.url}"
class VehicleListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    gumtree_url = models.ForeignKey(ListingUrl, on_delete=models.CASCADE,null=True,blank=True)
    gumtree_profile = models.ForeignKey(GumtreeProfileListing, on_delete=models.CASCADE,null=True,blank=True)
    facebook_profile = models.ForeignKey(FacebookProfileListing, on_delete=models.CASCADE,null=True,blank=True)
    list_id = models.CharField(max_length=255)
    year = models.CharField(max_length=255,null=True,blank=True)
    body_type = models.CharField(max_length=255,null=True,blank=True)
    fuel_type = models.CharField(max_length=255,null=True,blank=True)
    color = models.CharField(max_length=255,null=True,blank=True)
    variant = models.CharField(max_length=255,null=True,blank=True)
    make = models.CharField(max_length=100,null=True,blank=True)
    model = models.CharField(max_length=100,null=True,blank=True)
    price = models.CharField(max_length=255,null=True,blank=True)
    mileage = models.IntegerField(null=True,blank=True)
    exterior_colour = models.CharField(max_length=255,null=True,blank=True)
    interior_colour = models.CharField(max_length=255,null=True,blank=True)
    description = models.TextField(null=True,blank=True)
    condition = models.CharField(max_length=255,null=True,blank=True)
    transmission=models.CharField(max_length=255,null=True,blank=True)
    images = models.JSONField(null=True,blank=True)  # Store image URLs as JSON
    location = models.CharField(max_length=255,null=True,blank=True)
    url = models.URLField(null=True,blank=True)
    seller_profile_id = models.CharField(max_length=255,null=True,blank=True)
    status = models.CharField(max_length=255, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.year} {self.make} {self.model}"
class FacebookListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    listing = models.ForeignKey(VehicleListing, on_delete=models.CASCADE)
    status = models.CharField(max_length=255, null=True)
    error_message = models.TextField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.listing.make} {self.listing.model}"