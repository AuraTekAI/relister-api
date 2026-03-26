from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0018_alter_invoice_options_invoice_base_plan_charge_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='discount_code_str',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
    ]
