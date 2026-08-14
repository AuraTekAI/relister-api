# Data migration: Populate VehicleListing columns from Vehicle table
from django.db import migrations

def populate_vehiclelisting_data(apps, schema_editor):
    """Copy vehicle data from Vehicle table to VehicleListing table"""
    VehicleListing = apps.get_model('VehicleListing', 'VehicleListing')
    Vehicle = apps.get_model('VehicleListing', 'Vehicle')
    
    # Get all listings with vehicles
    listings_with_vehicles = VehicleListing.objects.filter(
        vehicle_id__isnull=False
    ).select_related('vehicle')
    
    count = 0
    for listing in listings_with_vehicles:
        vehicle = listing.vehicle
        if vehicle:
            # Copy vehicle data to listing
            listing.year = vehicle.year
            listing.make = vehicle.make
            listing.model = vehicle.model
            listing.body_type = vehicle.body_type
            listing.fuel_type = vehicle.fuel_type
            listing.color = vehicle.color
            listing.variant = vehicle.variant
            listing.mileage = vehicle.mileage
            listing.mileage_unavailable = False  # We have mileage from vehicle
            listing.vin = vehicle.vin
            listing.transmission = vehicle.transmission
            listing.save()
            count += 1
    
    print(f"✅ Populated {count} VehicleListing records from Vehicle data")

def reverse_populate(apps, schema_editor):
    """Reverse: Clear the data from VehicleListing columns"""
    VehicleListing = apps.get_model('VehicleListing', 'VehicleListing')
    VehicleListing.objects.all().update(
        year=None,
        make=None,
        model=None,
        body_type=None,
        fuel_type=None,
        color=None,
        variant=None,
        mileage=None,
        vin=None,
        transmission=None
    )
    print("✅ Cleared VehicleListing data columns")

class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0052_add_vehicle_model'),
    ]

    operations = [
        migrations.RunPython(populate_vehiclelisting_data, reverse_populate),
    ]
