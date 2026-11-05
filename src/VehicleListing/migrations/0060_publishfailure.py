# Server-side failed-publish history (see PublishFailure in models.py). The
# extension previously tracked this only in chrome.storage.local, so the count
# was lost on "Retry now", manual/admin republish, reinstall or a new computer.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0059_gumtreesyncrun'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PublishFailure',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fail_count', models.PositiveIntegerField(default=0)),
                ('last_fail_at', models.DateTimeField()),
                ('unknown_outcome', models.BooleanField(default=False)),
                ('reason', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='publish_failures', to='VehicleListing.vehiclelisting')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='publish_failures', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user', 'listing')},
            },
        ),
        migrations.AddIndex(
            model_name='publishfailure',
            index=models.Index(fields=['user', 'last_fail_at'], name='vl_pubfail_user_last_idx'),
        ),
    ]
