"""Vehicle listing lifecycle tracking: first_listed_at (set once, never
overwritten by relists), delisted_at, days_to_sell (computed on sale), and
lifecycle_status (active/sold/withdrawn) — a new field kept in sync
automatically alongside the existing pending/failed/completed/sold `status`
state machine, which is left completely untouched. relist_count already
existed and needs no migration.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0044_vehiclelisting_minimal_schema"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehiclelisting",
            name="first_listed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="vehiclelisting",
            name="delisted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="vehiclelisting",
            name="days_to_sell",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="vehiclelisting",
            name="lifecycle_status",
            field=models.CharField(
                choices=[("active", "Active"), ("sold", "Sold"), ("withdrawn", "Withdrawn")],
                default="active",
                max_length=20,
            ),
        ),
    ]
