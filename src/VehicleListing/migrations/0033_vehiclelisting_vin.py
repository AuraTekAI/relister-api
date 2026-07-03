from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0032_vehiclelisting_mileage_unavailable"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehiclelisting",
            name="vin",
            field=models.CharField(blank=True, max_length=17, null=True),
        ),
    ]
