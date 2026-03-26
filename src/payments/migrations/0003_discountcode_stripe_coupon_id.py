from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0002_subscription_cancel_at_period_end'),
    ]

    operations = [
        migrations.AddField(
            model_name='discountcode',
            name='stripe_coupon_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
