from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_user_account_status_user_first_name_user_last_name_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='dealership_license_number',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='dealership_license_phone',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
