# Generated migration to add missing ForeignKey fields that were in the model but missing from the database

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0044_fbverificationevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclelisting',
            name='gumtree_profile',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='VehicleListing.gumtreeprofilelisting'),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='facebook_profile',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='VehicleListing.facebookprofilelisting'),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='custom_domain_profile',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='VehicleListing.customdomainprofilelisting'),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='custom_domain_url',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='custom_domain_vehicle_listings', to='VehicleListing.listingurl'),
        ),
    ]
