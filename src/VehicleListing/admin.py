from django.contrib import admin

# Register your models here.
from .models import VehicleListing, ListingUrl

admin.site.register(VehicleListing)
admin.site.register(ListingUrl)
