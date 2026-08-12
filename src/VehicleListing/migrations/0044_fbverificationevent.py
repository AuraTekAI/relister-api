# Generated migration for FBVerificationEvent model

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('VehicleListing', '0043_vehiclelisting_dealer_matching_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='FBVerificationEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('detected', 'Verification wall detected'), ('cleared', 'Verification wall cleared')], max_length=20)),
                ('wall_type', models.CharField(blank=True, choices=[('checkpoint', 'Checkpoint'), ('confirm', 'Email/Phone confirmation'), ('disabled', 'Account disabled/restricted'), ('id_verify', 'Identity verification'), ('unknown', 'Unknown wall type')], max_length=40, null=True)),
                ('reason', models.CharField(blank=True, max_length=255, null=True)),
                ('detected_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='fb_verification_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='fbverificationevent',
            index=models.Index(fields=['user', 'status', 'created_at'], name='VehicleListing_fbverif_user_id_status_created_idx'),
        ),
        migrations.AddIndex(
            model_name='fbverificationevent',
            index=models.Index(fields=['status', 'created_at'], name='VehicleListing_fbverif_status_created_idx'),
        ),
    ]
