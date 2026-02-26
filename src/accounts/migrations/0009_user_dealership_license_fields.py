from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_user_last_delete_listing_time"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="dealership_license_number",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="dealership_license_phone_number",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
