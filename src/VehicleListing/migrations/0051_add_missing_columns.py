# Add all missing columns to VehicleListing table

from django.db import migrations, models
from decimal import Decimal

class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0050_create_complete_vehiclelisting_table'),
    ]

    operations = [
        # Add missing vehicle detail columns
        migrations.AddField(
            model_name='vehiclelisting',
            name='body_type',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='fuel_type',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='color',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='variant',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='mileage',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='mileage_unavailable',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='vin',
            field=models.CharField(blank=True, max_length=17, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='exterior_colour',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='interior_colour',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='condition',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='transmission',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='images',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='location',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='url',
            field=models.URLField(blank=True, null=True),
        ),
        # Add listing-related fields
        migrations.AddField(
            model_name='vehiclelisting',
            name='facebook_listing_id',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='is_relist',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='is_changed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='is_listed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='stripe_overage_reported',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='sold_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='rate',
            field=models.DecimalField(decimal_places=2, default=Decimal('2.00'), max_digits=5),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='has_images',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='sales',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='total_view_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
