"""
Shared Vehicle dedup/matching logic, used by both gumtree_scraper.py and
custom_domain_scraper.py so a single physical car — identified by VIN — is
represented by one Vehicle row shared across listings/users, instead of a
fresh row per scrape.

Matching rule: a VIN match is only reused in place when the physical-identity
columns (make/model/year/color/body_type/fuel_type/transmission) either agree
with what's already stored or were previously unknown (blank) on that row. A
VIN uniquely identifies one real car, so those shouldn't legitimately change;
if one of them genuinely conflicts with what's stored, that's a data anomaly
(bad VIN read, mixed-up ad, etc.), not a second car sharing a VIN — so rather
than corrupt the existing row, a NEW Vehicle row is created for this listing
instead. Since `Vehicle.vin` is a unique column, that new row is created
without the VIN set (two rows can't share it), and the conflict is logged so
it can be investigated.

Mileage is deliberately excluded from the identity check — an odometer
reading is expected to increase over the same car's life and is always safe
to update in place.
"""
import logging

from .models import Vehicle, VehicleImage

logger = logging.getLogger('vehicle_matching')

IDENTITY_FIELDS = ("make", "model", "variant", "year", "color", "body_type", "fuel_type", "transmission")


def sanitize_positive_price(raw_price, existing_price=None):
    """
    Rejects a missing/non-numeric/non-positive price (e.g. a scrape glitch
    producing -700, or 0) instead of persisting it, falling back to whatever
    price was already stored (None on first create) so a bad scrape can't
    silently corrupt a good existing value. Returns str(raw_price) — same
    type VehicleListing.price is already stored as (CharField) — on success.
    """
    if raw_price is None:
        return existing_price
    try:
        numeric_price = float(raw_price)
    except (TypeError, ValueError):
        logger.warning("Rejected non-numeric price %r", raw_price)
        return existing_price
    if numeric_price <= 0:
        logger.warning("Rejected non-positive price %r", raw_price)
        return existing_price
    return str(raw_price)


def _sanitize_mileage(raw_mileage, existing_mileage):
    """
    Rejects a negative odometer reading (e.g. a scrape glitch) instead of
    persisting it, falling back to whatever mileage was already stored. Zero
    is a valid mileage (brand-new vehicle) so only negative is rejected.
    Returns an int — same type Vehicle.mileage is already stored as
    (IntegerField) — on success.
    """
    if raw_mileage is None:
        return existing_mileage
    try:
        mileage = int(raw_mileage)
    except (TypeError, ValueError):
        logger.warning("Rejected non-numeric mileage %r", raw_mileage)
        return existing_mileage
    if mileage < 0:
        logger.warning("Rejected negative mileage %r", raw_mileage)
        return existing_mileage
    return mileage


def _identity_conflicts(vehicle, result):
    conflicts = []
    for field in IDENTITY_FIELDS:
        existing = getattr(vehicle, field)
        incoming = result.get(field)
        if existing and incoming and str(existing).strip().lower() != str(incoming).strip().lower():
            conflicts.append((field, existing, incoming))
    return conflicts


def sync_vehicle_from_result(vehicle, result):
    """Overwrite a Vehicle's attributes + image set from a freshly scraped result dict."""
    vehicle.year = result.get("year")
    vehicle.make = result.get("make")
    vehicle.model = result.get("model")
    vehicle.variant = result.get("variant")
    vehicle.body_type = result.get("body_type")
    vehicle.fuel_type = result.get("fuel_type")
    vehicle.color = result.get("color")
    vehicle.mileage = _sanitize_mileage(result.get("mileage"), vehicle.mileage)
    vehicle.transmission = result.get("transmission")
    vehicle.save()
    vehicle.images.all().delete()
    for image_url in (result.get("image") or []):
        if image_url:
            VehicleImage.objects.create(vehicle=vehicle, image_url=image_url)


def get_or_create_vehicle(result):
    """Find-or-create the Vehicle for a freshly scraped listing result dict.
    See module docstring for the VIN-match / identity-conflict rules."""
    vin = (result.get("vin") or "").strip() or None

    if not vin:
        # No reliable identity key — never guess at a match, always a fresh row.
        vehicle = Vehicle.objects.create()
        sync_vehicle_from_result(vehicle, result)
        return vehicle

    existing = Vehicle.objects.filter(vin=vin).first()
    if existing is None:
        vehicle = Vehicle.objects.create(vin=vin)
        sync_vehicle_from_result(vehicle, result)
        return vehicle

    conflicts = _identity_conflicts(existing, result)
    if conflicts:
        logger.warning(
            "VIN %s already exists but identity fields differ (%s) — treating this "
            "as a different vehicle; creating a new (VIN-less) Vehicle row instead "
            "of overwriting the existing one.",
            vin, conflicts,
        )
        vehicle = Vehicle.objects.create()
        sync_vehicle_from_result(vehicle, result)
        return vehicle

    sync_vehicle_from_result(existing, result)
    return existing
