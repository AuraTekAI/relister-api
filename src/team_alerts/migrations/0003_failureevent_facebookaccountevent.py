# Failure timeline (publish/delete/form-fill) for the spike monitor, and the
# per-dealer Facebook account event log (restricted / checkpoint / logged out).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('team_alerts', '0002_teamalertlog_old_listings_count'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FailureEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('publish', 'Publish failed'), ('delete', 'Delete failed'), ('form_fill', 'Facebook form fill failed')], max_length=20)),
                ('listing_id', models.BigIntegerField(blank=True, null=True)),
                ('reason', models.TextField(blank=True, null=True)),
                ('occurred_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='failure_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-occurred_at']},
        ),
        migrations.AddIndex(
            model_name='failureevent',
            index=models.Index(fields=['kind', 'occurred_at'], name='ta_failevent_kind_at_idx'),
        ),
        migrations.CreateModel(
            name='FacebookAccountEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('issue_type', models.CharField(choices=[('restricted', 'Account restricted / locked'), ('checkpoint', 'Security checkpoint / verification wall'), ('logged_out', 'Logged out of Facebook')], max_length=20)),
                ('status', models.CharField(choices=[('detected', 'Detected'), ('resolved', 'Resolved')], default='detected', max_length=10)),
                ('detail', models.CharField(blank=True, max_length=500, null=True)),
                ('fb_user_id', models.CharField(blank=True, max_length=40, null=True)),
                ('extension_version', models.CharField(blank=True, max_length=32, null=True)),
                ('detected_at', models.DateTimeField()),
                ('alerted', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='fb_account_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-detected_at']},
        ),
        migrations.AddIndex(
            model_name='facebookaccountevent',
            index=models.Index(fields=['user', 'issue_type', 'detected_at'], name='ta_fbacct_user_type_at_idx'),
        ),
    ]
