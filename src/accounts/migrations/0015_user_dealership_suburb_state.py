from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_rename_dnacarsales_dealership_url_user_custom_domain_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='dealership_suburb',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='dealership_state',
            field=models.CharField(
                blank=True,
                choices=[
                    ('WA', 'Western Australia'),
                    ('NSW', 'New South Wales'),
                    ('VIC', 'Victoria'),
                    ('QLD', 'Queensland'),
                    ('SA', 'South Australia'),
                    ('TAS', 'Tasmania'),
                    ('ACT', 'Australian Capital Territory'),
                    ('NT', 'Northern Territory'),
                ],
                max_length=3,
                null=True,
            ),
        ),
    ]
