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

IDENTITY_FIELDS = ("make", "model", "year", "color", "body_type", "fuel_type", "transmission")


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
    vehicle.body_type = result.get("body_type")
    vehicle.fuel_type = result.get("fuel_type")
    vehicle.color = result.get("color")
    vehicle.mileage = result.get("mileage")
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
