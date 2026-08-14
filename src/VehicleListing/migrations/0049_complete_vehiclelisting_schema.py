# Complete VehicleListing schema - run this to ensure all tables exist with all columns in postgres database

from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0048_merge_migrations'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # This ensures the complete VehicleListing table exists
        migrations.RunSQL("""
            ALTER TABLE IF EXISTS "VehicleListing_vehiclelisting"
            ADD COLUMN IF NOT EXISTS year VARCHAR(255);
        """, reverse_sql=""),

        migrations.RunSQL("""
            ALTER TABLE IF EXISTS "VehicleListing_vehiclelisting"
            ADD COLUMN IF NOT EXISTS make VARCHAR(100);
        """, reverse_sql=""),

        migrations.RunSQL("""
            ALTER TABLE IF EXISTS "VehicleListing_vehiclelisting"
            ADD COLUMN IF NOT EXISTS model VARCHAR(100);
        """, reverse_sql=""),
    ]
