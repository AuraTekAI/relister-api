# Lazy image pipeline: adds the 'queued' status choice to VehicleListingImage
# (slot claimed by ensure_listing_image_ingest — a Celery task is enqueued but
# hasn't run yet). Choices are app-level only; this migration changes no SQL
# schema, it just keeps model state in sync so makemigrations stays clean.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0056_drop_duplicated_spec_columns'),
    ]

    operations = [
        migrations.AlterField(
            model_name='vehiclelistingimage',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('queued', 'Queued'),
                    ('processing', 'Processing'),
                    ('ready', 'Ready'),
                    ('failed', 'Failed'),
                ],
                default='pending',
                max_length=10,
            ),
        ),
    ]
