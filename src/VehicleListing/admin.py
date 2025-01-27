from django.contrib import admin

# Register your models here.
from .models import VehicleListing, import_listing_from_url

admin.site.register(VehicleListing)
admin.site.register(import_listing_from_url)
