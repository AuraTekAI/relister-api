# Hand-written: ExtensionSyncStatus (per-dealer sync heartbeat + FB status).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0039_rename_vl_unpub_user_synced_idx_vehiclelist_user_id_bc489e_idx_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ExtensionSyncStatus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(blank=True, choices=[('gumtree', 'Gumtree'), ('customdomain', 'Custom Domain')], max_length=32, null=True)),
                ('status', models.CharField(choices=[('ok', 'OK'), ('verification_required', 'Facebook verification required'), ('fb_error', 'Facebook load error'), ('rate_limited', 'Rate limited'), ('no_facebook', 'Not logged in to Facebook')], default='ok', max_length=40)),
                ('status_detail', models.CharField(blank=True, max_length=255, null=True)),
                ('fb_count', models.IntegerField(default=0)),
                ('unpublished_count', models.IntegerField(default=0)),
                ('extension_version', models.CharField(blank=True, max_length=32, null=True)),
                ('synced_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ext_sync_status', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='extensionsyncstatus',
            index=models.Index(fields=['status', 'synced_at'], name='vl_extsync_status_idx'),
        ),
    ]
