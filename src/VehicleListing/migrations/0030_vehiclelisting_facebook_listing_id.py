from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0029_clear_mileage_only_descriptions'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclelisting',
            name='facebook_listing_id',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
    ]
