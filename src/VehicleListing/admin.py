from django.contrib import admin

# Register your models here
from .models import VehicleListing, ListingUrl
from .models import FacebookListing

admin.site.register(VehicleListing)
admin.site.register(ListingUrl)
admin.site.register(FacebookListing)