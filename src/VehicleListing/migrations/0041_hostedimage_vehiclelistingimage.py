# Hand-written: HostedImage (S3-hosted, content-hash-deduped photo) and
# VehicleListingImage (per-listing ordered slot linking a scraped source_url
# to a HostedImage) — see VehicleListing/image_pipeline.py for the pipeline
# that populates these.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0040_extensionsyncstatus'),
    ]

    operations = [
        migrations.CreateModel(
            name='HostedImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content_hash', models.CharField(db_index=True, max_length=64, unique=True)),
                ('source_url', models.URLField(blank=True, max_length=1000, null=True)),
                ('s3_key_thumbnail', models.CharField(blank=True, default='', max_length=512)),
                ('s3_key_medium', models.CharField(blank=True, default='', max_length=512)),
                ('s3_key_large', models.CharField(blank=True, default='', max_length=512)),
                ('width', models.PositiveIntegerField(blank=True, null=True)),
                ('height', models.PositiveIntegerField(blank=True, null=True)),
                ('file_size_bytes', models.PositiveIntegerField(blank=True, null=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('ready', 'Ready'), ('failed', 'Failed')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='VehicleListingImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_url', models.URLField(max_length=1000)),
                ('position', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('processing', 'Processing'), ('ready', 'Ready'), ('failed', 'Failed')], default='pending', max_length=10)),
                ('error_message', models.TextField(blank=True, null=True)),
                ('retry_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('hosted_image', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='listing_links', to='VehicleListing.hostedimage')),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='image_slots', to='VehicleListing.vehiclelisting')),
            ],
            options={
                'ordering': ['position'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='vehiclelistingimage',
            unique_together={('listing', 'source_url')},
        ),
        migrations.AddIndex(
            model_name='vehiclelistingimage',
            index=models.Index(fields=['listing', 'position'], name='vl_vli_listing_position_idx'),
        ),
        migrations.AddIndex(
            model_name='vehiclelistingimage',
            index=models.Index(fields=['status'], name='vl_vli_status_idx'),
        ),
    ]
