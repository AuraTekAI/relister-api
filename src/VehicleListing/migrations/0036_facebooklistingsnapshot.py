from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('VehicleListing', '0035_vehiclelisting_sold_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='FacebookListingSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fb_listing_id', models.CharField(max_length=64)),
                ('fb_url', models.URLField(blank=True, max_length=500, null=True)),
                ('title', models.CharField(blank=True, max_length=500, null=True)),
                ('price', models.CharField(blank=True, max_length=64, null=True)),
                ('fb_published_at', models.DateTimeField(blank=True, null=True)),
                ('days_on_facebook', models.IntegerField(blank=True, null=True)),
                ('is_aged', models.BooleanField(default=False)),
                ('is_duplicate', models.BooleanField(default=False)),
                ('duplicate_count', models.IntegerField(default=1)),
                ('mode', models.CharField(blank=True, choices=[('gumtree', 'Gumtree'), ('customdomain', 'Custom Domain')], max_length=32, null=True)),
                ('synced_at', models.DateTimeField(auto_now=True)),
                ('matched_listing', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fb_snapshots', to='VehicleListing.vehiclelisting')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='fb_snapshots', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='facebooklistingsnapshot',
            constraint=models.UniqueConstraint(fields=('user', 'fb_listing_id'), name='unique_user_fb_listing'),
        ),
        migrations.AddIndex(
            model_name='facebooklistingsnapshot',
            index=models.Index(fields=['user', 'synced_at'], name='vl_fbsnap_user_synced_idx'),
        ),
        migrations.AddIndex(
            model_name='facebooklistingsnapshot',
            index=models.Index(fields=['user', 'is_aged'], name='vl_fbsnap_user_aged_idx'),
        ),
    ]
