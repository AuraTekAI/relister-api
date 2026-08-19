"""Keep the Vehicle table (canonical spec data) in sync with VehicleListing rows.

Why this exists
---------------
The Vehicle table was added (migration 0052) to normalize spec attributes out
of VehicleListing, but no application code ever CREATED or LINKED Vehicle rows
— scrapers kept writing only the denormalized listing columns, so
`listing.vehicle` stayed NULL forever and every read that followed the
relationship found nothing (the "data is in Vehicle but the join returns
nothing" production issue; migration 0053 then papered over it by copying data
back onto the listing, i.e. duplication instead of a working relationship).

This module is the single place that maintains the relationship:

  * sync_vehicle_for_listing(listing)  — called by every scraper create/update
    path. Ensures the listing points at a Vehicle row and that the Vehicle row
    carries the freshest spec data.
  * backfill_vehicles(queryset)        — one-shot linker for rows created
    before this existed (also run by migration 0055).

Matching is deliberately as conservative as duplicate_matching.py: VIN first,
then an UNAMBIGUOUS structural match, always scoped to one (user,
seller_profile_id) dealer — two dealers listing lookalike cars must never share
a Vehicle row by accident. When in doubt a new Vehicle is created: a spare row
is cheap, a wrong merge corrupts two cars' data.
"""
import logging

from .duplicate_matching import is_valid_vin, normalize_for_matching

logger = logging.getLogger('vehicle_sync')

# Attributes owned by Vehicle. Name-for-name identical on both models so sync
# is a plain copy. (price/description/images/location/condition etc. stay
# listing-only: they describe the AD, not the car.)
VEHICLE_SPEC_FIELDS = (
    'vin', 'make', 'model', 'year', 'mileage',
    'transmission', 'fuel_type', 'body_type', 'color', 'variant',
)


def _spec_from_listing(listing):
    return {f: getattr(listing, f, None) for f in VEHICLE_SPEC_FIELDS}


def _dealer_vehicles(listing):
    """Vehicles already referenced by this dealer's listings (dealer-scoped
    candidate pool — Vehicle itself has no user FK by design, so scope is
    derived through the listings relation)."""
    from .models import Vehicle
    return Vehicle.objects.filter(
        listings__user_id=listing.user_id,
        listings__seller_profile_id=listing.seller_profile_id,
    ).distinct()


def _find_matching_vehicle(listing, spec):
    """VIN match first (strongest), else a structural match that survives only
    when exactly ONE candidate remains — mirrors duplicate_matching's
    refuse-to-guess policy."""
    candidates = _dealer_vehicles(listing)

    vin = spec.get('vin')
    if is_valid_vin(vin):
        by_vin = candidates.filter(vin__iexact=vin.strip()).first()
        if by_vin:
            return by_vin

    make, model, year = spec.get('make'), spec.get('model'), spec.get('year')
    if not (make and model and year):
        return None
    structural = list(candidates.filter(
        make__iexact=str(make).strip(),
        model__iexact=str(model).strip(),
        year=str(year).strip(),
    ))

    def soft(a, b):
        if not a or not b:
            return True
        return normalize_for_matching(a) == normalize_for_matching(b)

    structural = [
        v for v in structural
        if soft(spec.get('variant'), v.variant) and soft(spec.get('color'), v.color)
    ]
    if len(structural) == 1:
        return structural[0]
    return None  # ambiguous or nothing — caller creates a fresh Vehicle


def sync_vehicle_for_listing(listing):
    """Ensure `listing.vehicle` points at a Vehicle carrying the listing's
    current spec data. Idempotent; never raises (scrapers must not die over
    bookkeeping). Returns the Vehicle or None.

    Rules:
      * listing already linked      -> push fresh spec onto ITS vehicle
                                       (Vehicle stays the source of truth as
                                       the car's data changes between scrapes)
      * matching dealer vehicle     -> link to it and refresh its spec
      * otherwise                   -> create a new Vehicle from the listing
    """
    from .models import Vehicle
    try:
        spec = _spec_from_listing(listing)

        vehicle = listing.vehicle
        if vehicle is None:
            vehicle = _find_matching_vehicle(listing, spec)
        if vehicle is None:
            vehicle = Vehicle.objects.create(**spec)
            logger.info("Created Vehicle id=%s for listing id=%s (%s %s %s)",
                        vehicle.id, listing.id, spec.get('year'), spec.get('make'), spec.get('model'))
        else:
            changed = [f for f, val in spec.items() if getattr(vehicle, f) != val]
            if changed:
                for f in changed:
                    setattr(vehicle, f, spec[f])
                vehicle.save(update_fields=changed + ['updated_at'])
                logger.info("Updated Vehicle id=%s fields %s from listing id=%s",
                            vehicle.id, changed, listing.id)

        if listing.vehicle_id != vehicle.id:
            listing.vehicle = vehicle
            listing.save(update_fields=['vehicle', 'updated_at'])
        return vehicle
    except Exception:
        logger.exception("vehicle sync failed for listing id=%s — listing left unlinked",
                         getattr(listing, 'id', None))
        return None


def backfill_vehicles(queryset=None, batch_size=500):
    """Link every unlinked VehicleListing to a (possibly shared) Vehicle.
    Safe to re-run; processes oldest-first so the first listing of a car seeds
    the Vehicle and later duplicates attach to it. Returns (linked, created)."""
    from .models import Vehicle, VehicleListing
    qs = queryset if queryset is not None else VehicleListing.objects.all()
    qs = qs.filter(vehicle__isnull=True).order_by('id')
    linked = 0
    created_before = Vehicle.objects.count()
    for listing in qs.iterator(chunk_size=batch_size):
        if sync_vehicle_for_listing(listing) is not None:
            linked += 1
    created = Vehicle.objects.count() - created_before
    logger.info("backfill_vehicles: linked=%s vehicles_created=%s", linked, created)
    return linked, created
