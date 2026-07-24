"""Introduce Vehicle + VehicleImage: one record per physical vehicle, shared
across every Auto Relister user's listings, instead of the vehicle attributes
being duplicated on every per-user VehicleListing row.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0040_extensionsyncstatus"),
    ]

    operations = [
        migrations.CreateModel(
            name="Vehicle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("vin", models.CharField(blank=True, max_length=17, null=True)),
                ("make", models.CharField(blank=True, max_length=100, null=True)),
                ("model", models.CharField(blank=True, max_length=100, null=True)),
                ("year", models.CharField(blank=True, max_length=255, null=True)),
                ("mileage", models.IntegerField(blank=True, null=True)),
                ("transmission", models.CharField(blank=True, max_length=255, null=True)),
                ("fuel_type", models.CharField(blank=True, max_length=255, null=True)),
                ("body_type", models.CharField(blank=True, max_length=255, null=True)),
                ("color", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="vehicle",
            constraint=models.UniqueConstraint(
                condition=models.Q(("vin__isnull", False)),
                fields=("vin",),
                name="uniq_vehicle_vin",
            ),
        ),
        migrations.CreateModel(
            name="VehicleImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image_url", models.URLField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="images", to="VehicleListing.vehicle")),
            ],
        ),
    ]
