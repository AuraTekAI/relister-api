"""Add `mileage_unavailable` and backfill it for existing custom-domain rows.

Mileage is the tie-breaker the extension uses to distinguish several cars that
share a title. Custom-domain sources sometimes omit the odometer, leaving
mileage null/0; this flag marks those rows so they aren't treated as
distinguishable. Backfill is scoped to custom-domain rows only — Gumtree rows
always carry a parsed odometer and keep the default (False).
"""
from django.db import migrations, models


def backfill(apps, schema_editor):
    VehicleListing = apps.get_model("VehicleListing", "VehicleListing")
    rows = VehicleListing.objects.filter(
        custom_domain_profile__isnull=False,
        gumtree_profile__isnull=True,
    )
    flagged = 0
    for row in rows.iterator():
        unavailable = row.mileage is None or row.mileage == 0
        if unavailable != row.mileage_unavailable:
            row.mileage_unavailable = unavailable
            row.save(update_fields=["mileage_unavailable"])
            if unavailable:
                flagged += 1
    print(f"[mileage_unavailable backfill] custom-domain rows flagged={flagged}")


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0031_normalize_custom_domain_make"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehiclelisting",
            name="mileage_unavailable",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
