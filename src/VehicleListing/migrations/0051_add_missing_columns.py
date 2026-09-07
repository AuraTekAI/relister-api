# HISTORICAL NO-OP (kept so databases that recorded this name stay consistent).
#
# This migration originally re-ADDED ~24 VehicleListing columns (body_type,
# fuel_type, color, mileage, vin, images, ...) that already exist in the
# migration state from 0001_initial and earlier feature migrations
# (0032_mileage_unavailable, 0035_sold_at, ...). On a fresh database every
# AddField failed with DuplicateColumn. Nothing was ever actually missing;
# the operations were removed.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0050_create_complete_vehiclelisting_table'),
    ]

    operations = []
