# HISTORICAL NO-OP (kept so databases that recorded this name stay consistent).
#
# This migration originally re-ADDED the gumtree_profile / facebook_profile /
# custom_domain_profile / custom_domain_url ForeignKeys — but every one of those
# fields has been part of the migration state since 0001_initial (and the
# 0024 dnacarsales→custom_domain rename). Applying it on any freshly-migrated
# database therefore failed with `DuplicateColumn: gumtree_profile_id`, which is
# exactly what broke fresh deploys and made the test suite unable to build its
# database. The fields were never missing; the operations were removed.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0044_fbverificationevent'),
    ]

    operations = []
