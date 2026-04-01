from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0019_invoice_discount_code_str'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclelisting',
            name='is_listed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='vehiclelisting',
            name='relist_count',
            field=models.IntegerField(default=0),
        ),
    ]
