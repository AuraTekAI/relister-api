# Vehicle ↔ VehicleListing relationship repair.
#
# 1. AlterField vehicle FK: CASCADE → SET_NULL (deleting a Vehicle must never
#    destroy the dealer's listings) + related_name='listings' so the dealer-
#    scoped reverse lookup used by vehicle_sync reads naturally.
# 2. Backfill: every VehicleListing created before vehicle_sync existed has
#    vehicle=NULL. Build canonical Vehicle rows FROM the listings' spec
#    columns — the correct direction; 0053 previously copied the other way —
#    deduping per (user, seller_profile_id) by VIN first, then by an
#    unambiguous make/model/year(+variant/color) key, so a car listed on both
#    Gumtree and the dealer's own site converges on ONE Vehicle row.
#
# The backfill logic is duplicated (not imported from vehicle_sync) because
# migrations must run against historical models via apps.get_model.
# (Also renames three drifted index names Django detected — no data impact.)

import django.db.models.deletion
from django.db import migrations, models


def _is_valid_vin(vin):
    if not vin:
        return False
    v = vin.strip().upper()
    return len(v) == 17 and v.isalnum() and len(set(v)) > 1


def _norm(value):
    import re
    if not value:
        return ''
    return re.sub(r'\s+', ' ', str(value).strip().upper())


SPEC_FIELDS = ('vin', 'make', 'model', 'year', 'mileage',
               'transmission', 'fuel_type', 'body_type', 'color', 'variant')


def backfill_vehicles(apps, schema_editor):
    Vehicle = apps.get_model('VehicleListing', 'Vehicle')
    VehicleListing = apps.get_model('VehicleListing', 'VehicleListing')

    # (user_id, seller_profile_id) -> {'by_vin': {...}, 'by_key': {...}} of
    # vehicles created/seen in this run, so duplicates converge without
    # re-querying per listing.
    linked = created = 0
    dealer_cache = {}

    qs = VehicleListing.objects.filter(vehicle__isnull=True).order_by('id')
    for listing in qs.iterator(chunk_size=500):
        scope = (listing.user_id, listing.seller_profile_id)
        cache = dealer_cache.setdefault(scope, {'by_vin': {}, 'by_key': {}})

        vehicle = None
        vin = listing.vin
        if _is_valid_vin(vin):
            vehicle = cache['by_vin'].get(_norm(vin))

        key = None
        if listing.make and listing.model and listing.year:
            key = (_norm(listing.make), _norm(listing.model), _norm(listing.year),
                   _norm(listing.variant), _norm(listing.color))
            if vehicle is None:
                vehicle = cache['by_key'].get(key)

        if vehicle is None:
            vehicle = Vehicle.objects.create(
                **{f: getattr(listing, f, None) for f in SPEC_FIELDS}
            )
            created += 1

        if _is_valid_vin(vin):
            cache['by_vin'][_norm(vin)] = vehicle
        if key is not None:
            cache['by_key'][key] = vehicle

        listing.vehicle = vehicle
        listing.save(update_fields=['vehicle'])
        linked += 1

    if linked or created:
        print(f"\n  [vehicle backfill] listings linked={linked}, vehicles created={created}")


def unlink_vehicles(apps, schema_editor):
    """Reverse: detach listings (Vehicle rows are left in place — harmless,
    and deleting them could not distinguish backfilled from organic rows)."""
    VehicleListing = apps.get_model('VehicleListing', 'VehicleListing')
    VehicleListing.objects.update(vehicle=None)


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0054_fix_foreignkey_columns'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='extensionsyncstatus',
            new_name='VehicleList_status_54ee15_idx',
            old_name='vl_extsync_status_idx',
        ),
        migrations.RenameIndex(
            model_name='fbverificationevent',
            new_name='VehicleList_user_id_efe229_idx',
            old_name='VehicleListing_fbverif_user_id_status_created_idx',
        ),
        migrations.RenameIndex(
            model_name='fbverificationevent',
            new_name='VehicleList_status_3820b1_idx',
            old_name='VehicleListing_fbverif_status_created_idx',
        ),
        migrations.AlterField(
            model_name='vehiclelisting',
            name='vehicle',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='listings', to='VehicleListing.vehicle'),
        ),
        migrations.RunPython(backfill_vehicles, unlink_vehicles),
    ]
