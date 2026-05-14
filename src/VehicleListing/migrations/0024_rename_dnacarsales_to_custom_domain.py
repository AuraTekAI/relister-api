import django.db.models.deletion
from django.db import migrations, models


def forwards_seller_profile_id(apps, schema_editor):
    VehicleListing = apps.get_model('VehicleListing', 'VehicleListing')
    CustomDomainProfileListing = apps.get_model('VehicleListing', 'CustomDomainProfileListing')

    VehicleListing.objects.filter(seller_profile_id='dnacarsales').update(
        seller_profile_id='www.dnacarsales.com.au'
    )
    CustomDomainProfileListing.objects.filter(profile_id='dnacarsales').update(
        profile_id='www.dnacarsales.com.au',
        domain='www.dnacarsales.com.au',
    )


def backwards_seller_profile_id(apps, schema_editor):
    VehicleListing = apps.get_model('VehicleListing', 'VehicleListing')
    CustomDomainProfileListing = apps.get_model('VehicleListing', 'CustomDomainProfileListing')

    VehicleListing.objects.filter(seller_profile_id='www.dnacarsales.com.au').update(
        seller_profile_id='dnacarsales'
    )
    CustomDomainProfileListing.objects.filter(profile_id='www.dnacarsales.com.au').update(
        profile_id='dnacarsales',
        domain=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0023_vehiclelisting_dnacarsales_url_and_more'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='DNACarSalesProfileListing',
            new_name='CustomDomainProfileListing',
        ),
        migrations.AddField(
            model_name='customdomainprofilelisting',
            name='domain',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.RenameField(
            model_name='vehiclelisting',
            old_name='dnacarsales_url',
            new_name='custom_domain_url',
        ),
        migrations.RenameField(
            model_name='vehiclelisting',
            old_name='dnacarsales_profile',
            new_name='custom_domain_profile',
        ),
        migrations.AlterField(
            model_name='vehiclelisting',
            name='custom_domain_url',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='custom_domain_vehicle_listings',
                to='VehicleListing.listingurl',
            ),
        ),
        migrations.RunPython(forwards_seller_profile_id, backwards_seller_profile_id),
    ]
