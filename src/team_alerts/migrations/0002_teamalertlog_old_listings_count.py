from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('team_alerts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='teamalertlog',
            name='old_listings_count',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
