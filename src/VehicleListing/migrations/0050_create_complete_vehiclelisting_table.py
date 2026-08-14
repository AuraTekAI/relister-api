# Complete VehicleListing table creation with all required columns

from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0048_merge_migrations'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='VehicleListing_Complete',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('list_id', models.CharField(blank=True, max_length=255, null=True)),
                ('year', models.CharField(blank=True, max_length=255, null=True)),
                ('body_type', models.CharField(blank=True, max_length=255, null=True)),
                ('fuel_type', models.CharField(blank=True, max_length=255, null=True)),
                ('color', models.CharField(blank=True, max_length=255, null=True)),
                ('variant', models.CharField(blank=True, max_length=255, null=True)),
                ('make', models.CharField(blank=True, max_length=100, null=True)),
                ('model', models.CharField(blank=True, max_length=100, null=True)),
                ('price', models.CharField(blank=True, max_length=255, null=True)),
                ('mileage', models.IntegerField(blank=True, null=True)),
                ('mileage_unavailable', models.BooleanField(default=False)),
                ('vin', models.CharField(blank=True, max_length=17, null=True)),
                ('exterior_colour', models.CharField(blank=True, max_length=255, null=True)),
                ('interior_colour', models.CharField(blank=True, max_length=255, null=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('condition', models.CharField(blank=True, max_length=255, null=True)),
                ('transmission', models.CharField(blank=True, max_length=255, null=True)),
                ('images', models.JSONField(blank=True, null=True)),
                ('location', models.CharField(blank=True, max_length=255, null=True)),
                ('url', models.URLField(blank=True, null=True)),
                ('seller_profile_id', models.CharField(blank=True, max_length=255, null=True)),
                ('facebook_listing_id', models.CharField(blank=True, max_length=128, null=True)),
                ('status', models.CharField(blank=True, max_length=255, null=True)),
                ('is_relist', models.BooleanField(default=False)),
                ('is_changed', models.BooleanField(default=False)),
                ('is_listed', models.BooleanField(default=False)),
                ('stripe_overage_reported', models.BooleanField(default=False)),
                ('relist_count', models.IntegerField(default=0)),
                ('sold_at', models.DateTimeField(blank=True, null=True)),
                ('rate', models.DecimalField(decimal_places=2, default=Decimal('2.00'), max_digits=5)),
                ('retry_count', models.IntegerField(default=0)),
                ('listed_on', models.DateTimeField(blank=True, null=True)),
                ('has_images', models.BooleanField(default=False)),
                ('sales', models.BooleanField(default=False)),
                ('total_view_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('custom_domain_profile_id', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='VehicleListing.customdomainprofilelisting')),
                ('custom_domain_url_id', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='custom_domain_vehicle_listings', to='VehicleListing.listingurl')),
                ('facebook_profile_id', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='VehicleListing.facebookprofilelisting')),
                ('gumtree_profile_id', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='VehicleListing.gumtreeprofilelisting')),
                ('gumtree_url_id', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='VehicleListing.listingurl')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'VehicleListing_vehiclelisting',
            },
        ),
    ]
