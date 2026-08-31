# Dealer stock number scraped from the source listing (Gumtree exposes it as an
# optional ad detail attribute). Non-null with default "1" so listings whose ad
# carries no stock number — and every pre-existing row this migration backfills —
# still hold a usable value rather than NULL.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0057_vehiclelistingimage_queued_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclelisting',
            name='stock_number',
            field=models.CharField(default='1', max_length=255),
        ),
    ]
