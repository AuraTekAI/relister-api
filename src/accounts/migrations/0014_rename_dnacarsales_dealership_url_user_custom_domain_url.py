from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_user_dnacarsales_dealership_url'),
    ]

    operations = [
        migrations.RenameField(
            model_name='user',
            old_name='dnacarsales_dealership_url',
            new_name='custom_domain_url',
        ),
    ]
