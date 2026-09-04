# Empty migration after all fields have been added

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0045_add_missing_foreign_keys'),
    ]

    operations = [
    ]
