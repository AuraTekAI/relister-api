# Generated by Django 5.0.8 on 2025-03-13 07:32

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0004_rename_email_facebookusercredentials_username'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='facebookusercredentials',
            name='password',
        ),
        migrations.RemoveField(
            model_name='facebookusercredentials',
            name='username',
        ),
    ]
