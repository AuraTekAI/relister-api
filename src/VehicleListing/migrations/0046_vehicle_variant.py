"""Restore `variant` (trim/spec level), this time on Vehicle rather than
VehicleListing — every scraper/adapter already extracts it into its result
dict, it just wasn't stored anywhere since the Vehicle/VehicleListing split.
Needed for like-for-like matching in the price-estimation feature (R17).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0045_vehiclelisting_lifecycle_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicle",
            name="variant",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
