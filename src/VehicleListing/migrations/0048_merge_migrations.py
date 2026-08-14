# Empty merge migration to resolve conflict and mark 0047_add_remaining_missing_fields as applied

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0045_add_missing_foreign_keys'),
        ('VehicleListing', '0047_add_remaining_missing_fields'),
    ]

    operations = [
    ]
