# HISTORICAL NO-OP (kept so databases that recorded this name stay consistent).
#
# This migration originally created a phantom model `VehicleListing_Complete`
# mapped to db_table='VehicleListing_vehiclelisting' — the SAME physical table
# as the real VehicleListing model. On a fresh database that meant
# `DuplicateTable: relation "VehicleListing_vehiclelisting" already exists`.
# Worse, its FK fields were named `gumtree_url_id`, `facebook_profile_id`, ...
# so Django generated columns like `gumtree_url_id_id`, which 0054 then had to
# rename back with raw SQL. The phantom model was never referenced by any code.
# All operations were removed; the real table comes from 0001_initial.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0048_merge_migrations'),
    ]

    operations = []
