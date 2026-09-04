# Finish the Vehicle normalization: DROP the spec columns VehicleListing
# duplicated from Vehicle (vin/make/model/year/mileage/transmission/fuel_type/
# body_type/color/variant).
#
# Why: since 0055 every listing is linked to a canonical Vehicle row carrying
# IDENTICALLY-NAMED columns (so no rename/mapping is involved anywhere — the
# data already lives under the same column names in VehicleListing_vehicle),
# and every reader/writer goes through the relationship. Keeping a second copy
# on the listing was pure duplication and the root cause of the "API serves
# stale listing column instead of the Vehicle's value" class of bug.
#
# Order matters:
#   1. RunPython backfill — any listing still unlinked gets a Vehicle built
#      from its columns while they still exist. After this, dropping the
#      columns cannot lose data.
#   2. Remove the two dealer-dedup indexes that referenced the columns.
#   3. Remove the ten columns.
#   4. Add replacement indexes on Vehicle (dedup + storefront search now
#      query through the listing→vehicle join).
#
# Reverse: fields/indexes are restored from state, then copy_spec_back
# repopulates the re-added columns from each listing's Vehicle row.
#
# Backfill logic is duplicated from 0055 (not imported from vehicle_sync)
# because migrations must run against historical models via apps.get_model.

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


def backfill_unlinked(apps, schema_editor):
    """Last-chance linker: identical to 0055's backfill, re-run here because
    rows created while 0055-era code was live but before vehicle_sync shipped
    (or with sync errored-out) may still be unlinked — and after this
    migration their columns are gone."""
    Vehicle = apps.get_model('VehicleListing', 'Vehicle')
    VehicleListing = apps.get_model('VehicleListing', 'VehicleListing')

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
        print(f"\n  [pre-drop backfill] listings linked={linked}, vehicles created={created}")


def copy_spec_back(apps, schema_editor):
    """Reverse of the whole migration's data effect: the columns have just
    been re-added (empty) by the reversed RemoveFields — refill them from each
    listing's canonical Vehicle row."""
    VehicleListing = apps.get_model('VehicleListing', 'VehicleListing')
    qs = VehicleListing.objects.filter(vehicle__isnull=False).select_related('vehicle')
    for listing in qs.iterator(chunk_size=500):
        for f in SPEC_FIELDS:
            setattr(listing, f, getattr(listing.vehicle, f, None))
        listing.save(update_fields=list(SPEC_FIELDS))


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0055_vehicle_fk_and_backfill'),
    ]

    operations = [
        migrations.RunPython(backfill_unlinked, copy_spec_back),
        migrations.RemoveIndex(
            model_name='vehiclelisting',
            name='vl_dealer_vin_idx',
        ),
        migrations.RemoveIndex(
            model_name='vehiclelisting',
            name='vl_dealer_mmy_idx',
        ),
        migrations.RemoveField(model_name='vehiclelisting', name='vin'),
        migrations.RemoveField(model_name='vehiclelisting', name='make'),
        migrations.RemoveField(model_name='vehiclelisting', name='model'),
        migrations.RemoveField(model_name='vehiclelisting', name='year'),
        migrations.RemoveField(model_name='vehiclelisting', name='mileage'),
        migrations.RemoveField(model_name='vehiclelisting', name='transmission'),
        migrations.RemoveField(model_name='vehiclelisting', name='fuel_type'),
        migrations.RemoveField(model_name='vehiclelisting', name='body_type'),
        migrations.RemoveField(model_name='vehiclelisting', name='color'),
        migrations.RemoveField(model_name='vehiclelisting', name='variant'),
        migrations.AddIndex(
            model_name='vehicle',
            index=models.Index(fields=['vin'], name='vehicle_vin_idx'),
        ),
        migrations.AddIndex(
            model_name='vehicle',
            index=models.Index(fields=['make', 'model', 'year'], name='vehicle_mmy_idx'),
        ),
    ]
