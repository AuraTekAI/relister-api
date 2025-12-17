from django.db import models
from accounts.models import User
from decimal import Decimal

class ListingUrl(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField(null=True,blank=True)
    listing_id = models.CharField(max_length=255,null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=255, null=True)
    error_message = models.TextField(null=True)
    def __str__(self):
        return f"{self.url}"
    
class FacebookUserCredentials(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    session_cookie = models.JSONField(null=True,blank=True,default=dict)
    status = models.BooleanField(default=False)
    status_reminder = models.BooleanField(default=False)
    retry_count = models.IntegerField(default=0)
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
    is_relist = models.BooleanField(default=False)
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('2.00'))
    retry_count = models.IntegerField(default=0)
    listed_on = models.DateTimeField(null=True,blank=True)
    has_images=models.BooleanField(default=False)
    sales = models.BooleanField(default=False)
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
    
class RelistingFacebooklisting(models.Model):
    listing = models.ForeignKey(VehicleListing, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    relisting_date=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=255, null=True)
    last_relisting_status = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.listing.make} {self.listing.model}"
    

class Invoice(models.Model):
    invoice_id = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    details = models.TextField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.invoice_id}"
