from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_user_dealership_license_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='listing_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='user',
            name='relist_cycles',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='user',
            name='overage_count',
            field=models.IntegerField(default=0),
        ),
    ]
