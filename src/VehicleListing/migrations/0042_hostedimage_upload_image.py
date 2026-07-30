# Hand-written: add HostedImage.upload_image — the FB-safe JPEG rendition the
# Chrome extension re-uploads to Facebook Marketplace (which rejects our WebP
# variants). Backfilled for existing rows via manage.py backfill_upload_variants.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0041_hostedimage_vehiclelistingimage'),
    ]

    operations = [
        migrations.AddField(
            model_name='hostedimage',
            name='upload_image',
            field=models.CharField(blank=True, default='', max_length=512),
        ),
    ]
