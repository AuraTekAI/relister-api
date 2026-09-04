# HISTORICAL NO-OP (kept so databases that recorded this name stay consistent).
#
# This migration originally copied Vehicle attributes BACK onto the duplicated
# VehicleListing columns (listing.make = vehicle.make, ...) — i.e. it "fixed"
# the broken Vehicle↔VehicleListing join by duplicating data, which is the
# exact anti-pattern the Vehicle table was meant to remove. The proper fix is
# the reverse direction: 0055 builds/links Vehicle rows FROM listings and the
# application now reads specs through the `listing.vehicle` relationship
# (see VehicleListing/vehicle_sync.py). On any fresh database this migration
# was a no-op anyway (no Vehicle rows exist at this point in the chain).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0052_add_vehicle_model'),
    ]

    operations = []
