# Generated by Django 5.0.8 on 2025-03-05 10:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0002_alter_facebookusercredentials_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclelisting',
            name='condition',
            field=models.CharField(blank=True, max_length=255, null=True),
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
    ]
