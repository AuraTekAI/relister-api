"""Data migration: populate the new Vehicle/VehicleImage tables from the
vehicle-attribute columns still present on VehicleListing at this point in
history, and link each VehicleListing row to its Vehicle.

Rows sharing the same non-blank VIN are collapsed onto a single Vehicle
(first-seen row's attributes win); every other row gets its own Vehicle since
there's no other reliable cross-user identity key.
"""
from django.db import migrations


def backfill_vehicles(apps, schema_editor):
    VehicleListing = apps.get_model("VehicleListing", "VehicleListing")
    Vehicle = apps.get_model("VehicleListing", "Vehicle")
    VehicleImage = apps.get_model("VehicleListing", "VehicleImage")

    vehicle_by_vin = {}
    created_vehicles = 0
    created_images = 0

    for listing in VehicleListing.objects.all().iterator(chunk_size=500):
        vin = (listing.vin or "").strip() or None
        vehicle = vehicle_by_vin.get(vin) if vin else None

        if vehicle is None:
            vehicle = Vehicle.objects.create(
                vin=vin,
                make=listing.make,
                model=listing.model,
                year=listing.year,
                mileage=listing.mileage,
                transmission=listing.transmission,
                fuel_type=listing.fuel_type,
                body_type=listing.body_type,
                color=listing.color,
            )
            created_vehicles += 1
            if vin:
                vehicle_by_vin[vin] = vehicle

            for image_url in (listing.images or []):
                if image_url:
                    VehicleImage.objects.create(vehicle=vehicle, image_url=image_url)
                    created_images += 1

        listing.vehicle_id = vehicle.id
        listing.save(update_fields=["vehicle"])

    if created_vehicles:
        print(f"  Created {created_vehicles} Vehicle rows and {created_images} VehicleImage rows from existing listings")


def noop_reverse(apps, schema_editor):
    # Vehicle/VehicleImage rows are dropped along with the tables when this
    # migration is reversed (0041's CreateModel reversal) — nothing to undo here.
    pass


class Migration(migrations.Migration):
    # Row-by-row RunPython over a potentially large table; keep it out of one
    # long-held transaction, matching the pattern used in 0025/0031.
    atomic = False

    dependencies = [
        ("VehicleListing", "0042_vehiclelisting_vehicle_facebook_url"),
    ]

    operations = [
        migrations.RunPython(backfill_vehicles, reverse_code=noop_reverse),
    ]
