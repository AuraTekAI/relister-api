"""Finish the Vehicle/VehicleListing split: drop the old per-user unique
constraint (it keyed on list_id, which is being removed), make `vehicle`
required now every row has been backfilled by 0043, and remove every
vehicle-attribute / profile-tracking / relist-bookkeeping field that moved
to Vehicle/VehicleImage or was dropped entirely under the new minimal
VehicleListing schema.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0043_backfill_vehicle_data"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="vehiclelisting",
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name="vehiclelisting",
            name="vehicle",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="listings",
                to="VehicleListing.vehicle",
            ),
        ),
        migrations.RemoveField(model_name="vehiclelisting", name="gumtree_profile"),
        migrations.RemoveField(model_name="vehiclelisting", name="facebook_profile"),
        migrations.RemoveField(model_name="vehiclelisting", name="custom_domain_url"),
        migrations.RemoveField(model_name="vehiclelisting", name="custom_domain_profile"),
        migrations.RemoveField(model_name="vehiclelisting", name="list_id"),
        migrations.RemoveField(model_name="vehiclelisting", name="year"),
        migrations.RemoveField(model_name="vehiclelisting", name="body_type"),
        migrations.RemoveField(model_name="vehiclelisting", name="fuel_type"),
        migrations.RemoveField(model_name="vehiclelisting", name="color"),
        migrations.RemoveField(model_name="vehiclelisting", name="variant"),
        migrations.RemoveField(model_name="vehiclelisting", name="make"),
        migrations.RemoveField(model_name="vehiclelisting", name="model"),
        migrations.RemoveField(model_name="vehiclelisting", name="mileage"),
        migrations.RemoveField(model_name="vehiclelisting", name="mileage_unavailable"),
        migrations.RemoveField(model_name="vehiclelisting", name="vin"),
        migrations.RemoveField(model_name="vehiclelisting", name="exterior_colour"),
        migrations.RemoveField(model_name="vehiclelisting", name="interior_colour"),
        migrations.RemoveField(model_name="vehiclelisting", name="condition"),
        migrations.RemoveField(model_name="vehiclelisting", name="transmission"),
        migrations.RemoveField(model_name="vehiclelisting", name="images"),
        migrations.RemoveField(model_name="vehiclelisting", name="location"),
        migrations.RemoveField(model_name="vehiclelisting", name="url"),
        migrations.RemoveField(model_name="vehiclelisting", name="facebook_listing_id"),
        migrations.RemoveField(model_name="vehiclelisting", name="is_relist"),
        migrations.RemoveField(model_name="vehiclelisting", name="is_changed"),
        migrations.RemoveField(model_name="vehiclelisting", name="is_listed"),
        migrations.RemoveField(model_name="vehiclelisting", name="stripe_overage_reported"),
        migrations.RemoveField(model_name="vehiclelisting", name="sold_at"),
        migrations.RemoveField(model_name="vehiclelisting", name="rate"),
        migrations.RemoveField(model_name="vehiclelisting", name="has_images"),
        migrations.RemoveField(model_name="vehiclelisting", name="sales"),
        migrations.RemoveField(model_name="vehiclelisting", name="total_view_count"),
    ]
