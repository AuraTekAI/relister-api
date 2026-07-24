"""Add the (initially nullable) vehicle FK and the new facebook_url field to
VehicleListing. The FK is backfilled by 0043 and tightened to NOT NULL by
0044, once every row has a Vehicle to point at.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0041_vehicle_vehicleimage"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehiclelisting",
            name="vehicle",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="listings",
                to="VehicleListing.vehicle",
            ),
        ),
        migrations.AddField(
            model_name="vehiclelisting",
            name="facebook_url",
            field=models.URLField(blank=True, max_length=500, null=True),
        ),
    ]
