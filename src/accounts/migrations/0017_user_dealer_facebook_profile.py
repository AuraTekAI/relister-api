from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_user_dealership_address'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='dealer_facebook_profile',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
