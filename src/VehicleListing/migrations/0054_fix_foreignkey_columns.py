# HISTORICAL NO-OP (kept so databases that recorded this name stay consistent).
#
# This migration originally ran raw `ALTER TABLE ... RENAME COLUMN
# "gumtree_profile_id_id" TO "gumtree_profile_id"` (and four siblings) to undo
# the damage done by 0050's phantom model whose FK fields were misnamed
# `*_id`, producing `*_id_id` columns. With 0050 reduced to a no-op those
# broken columns are never created, so on every correctly-migrated database
# these renames would FAIL (the source column doesn't exist). The operations
# were removed.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0053_populate_vehiclelisting_from_vehicle'),
    ]

    operations = []
