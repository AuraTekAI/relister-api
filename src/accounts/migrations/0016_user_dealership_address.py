from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_user_dealership_suburb_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='dealership_address',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
