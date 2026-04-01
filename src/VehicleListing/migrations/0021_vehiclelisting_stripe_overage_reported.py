from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0020_vehiclelisting_is_listed_relist_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclelisting',
            name='stripe_overage_reported',
            field=models.BooleanField(default=False),
        ),
    ]
