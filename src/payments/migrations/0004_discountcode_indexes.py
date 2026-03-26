from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0003_discountcode_stripe_coupon_id'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='discountcode',
            index=models.Index(fields=['is_active'], name='payments_dc_is_active_idx'),
        ),
        migrations.AddIndex(
            model_name='discountcode',
            index=models.Index(fields=['valid_until'], name='payments_dc_valid_until_idx'),
        ),
    ]
