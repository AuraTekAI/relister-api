# Hand-written: UnpublishedListingSnapshot (backend listings not on Facebook + reason).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0037_remove_facebooklistingsnapshot_unique_user_fb_listing_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UnpublishedListingSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(blank=True, max_length=500, null=True)),
                ('price', models.CharField(blank=True, max_length=64, null=True)),
                ('images_count', models.IntegerField(default=0)),
                ('reason', models.CharField(blank=True, choices=[('SOLD', 'Sold on source'), ('INSUFFICIENT_IMAGES', 'Fewer than 2 images'), ('LOCATION_MISSING', 'No dealer location'), ('FAILED_HIDDEN', 'Failed repeatedly — hidden'), ('FAILED_COOLDOWN', 'In failure cooldown'), ('QUOTA_REACHED', 'Daily publish limit reached'), ('PENDING', 'Queued — not yet published')], max_length=40, null=True)),
                ('reason_detail', models.CharField(blank=True, max_length=255, null=True)),
                ('mode', models.CharField(blank=True, choices=[('gumtree', 'Gumtree'), ('customdomain', 'Custom Domain')], max_length=32, null=True)),
                ('synced_at', models.DateTimeField(auto_now=True)),
                ('listing', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='unpublished_snapshots', to='VehicleListing.vehiclelisting')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='unpublished_snapshots', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='unpublishedlistingsnapshot',
            index=models.Index(fields=['user', 'synced_at'], name='vl_unpub_user_synced_idx'),
        ),
        migrations.AddIndex(
            model_name='unpublishedlistingsnapshot',
            index=models.Index(fields=['user', 'reason'], name='vl_unpub_user_reason_idx'),
        ),
    ]
