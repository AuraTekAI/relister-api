# Generated migration to add remaining missing columns from VehicleListing model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0045_add_missing_foreign_keys'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclelisting',
            name='list_id',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
    ]
