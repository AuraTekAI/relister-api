# Hand-written: index-only migration supporting duplicate_matching.py's
# find_existing_vehicle() lookups (same dealer + VIN, or same dealer +
# make/model/year) so re-matching a relisted vehicle under a new source
# listing id doesn't require scanning every row for the dealer. No column,
# schema, or data change — safe to run without a backfill.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0042_hostedimage_upload_image'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='vehiclelisting',
            index=models.Index(fields=['user', 'seller_profile_id', 'vin'], name='vl_dealer_vin_idx'),
        ),
        migrations.AddIndex(
            model_name='vehiclelisting',
            index=models.Index(fields=['user', 'seller_profile_id', 'make', 'model', 'year'], name='vl_dealer_mmy_idx'),
        ),
    ]
