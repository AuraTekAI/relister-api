# Migration to add remaining missing fields (list_id was already added by 0046_add_missing_columns)

from django.db import migrations, models
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0045_add_missing_foreign_keys'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclelisting',
            name='year',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='body_type',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='fuel_type',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='color',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='variant',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='make',
            field=models.CharField(max_length=100, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='model',
            field=models.CharField(max_length=100, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='price',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='mileage',
            field=models.IntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='mileage_unavailable',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='vin',
            field=models.CharField(max_length=17, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='exterior_colour',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='interior_colour',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='description',
            field=models.TextField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='condition',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='transmission',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='images',
            field=models.JSONField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='location',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='url',
            field=models.URLField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='seller_profile_id',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='facebook_listing_id',
            field=models.CharField(max_length=128, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='status',
            field=models.CharField(max_length=255, null=True),
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
            name='relist_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='sold_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='rate',
            field=models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('2.00')),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='retry_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='listed_on',
            field=models.DateTimeField(null=True, blank=True),
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
