"""
Vehicle sale price estimation (R16-R21): estimates a realistic market price
for a vehicle from the median sale price of similar SOLD listings already in
our database. This is a statistical lookup over real closed sales, not a
prediction model.

Matching starts strict (make, model, variant, transmission, year ±1, same
mileage band — R17) and widens one criterion at a time, in the order defined
in WIDENING_TIERS (R19), until MIN_SAMPLE_SIZE sold vehicles are found or the
loosest tier is reached. make + model are NEVER relaxed — dropping either
would mean showing one model's price history for a different one, which
isn't a "similar vehicle" any more. If even the loosest tier has fewer than
MIN_SAMPLE_SIZE sold vehicles with a usable price, the result is
"insufficient data" rather than a number computed from too little to trust
(R20).

Admin-only (R21) — this reveals aggregate sale data across every dealer on
the platform, not just one dealer's own listings, so it's kept behind the
same authentication as the rest of Dealer Info and is never reachable from
the public storefront's API.
"""
import json
import re

from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from .export_utils import median_p25_p75
from .models import VehicleListing

MIN_SAMPLE_SIZE = 5

# Mileage bands in km. A vehicle's mileage falls into exactly one band;
# "same band" is the strict match, "adjacent band" (±1 index) is one
# widening step. Deliberately coarse — exact-km matches would almost never
# occur, but which ~20k-wide bracket a car falls into strongly affects price.
MILEAGE_BANDS = [0, 20_000, 40_000, 60_000, 80_000, 100_000, 150_000, 200_000]

# Each step widens exactly one thing relative to the previous tier, ordered
# from most to least price-sensitive criterion (see the module docstring in
# the PR/commit for the reasoning) — make/model are matched unconditionally
# and never appear here.
WIDENING_TIERS = [
    {
        "key": "exact",
        "variant": True, "transmission": True, "year_tolerance": 1, "mileage_band_tolerance": 0,
        "description": "Exact match on variant, transmission, year (±1) and mileage band",
    },
    {
        "key": "widen_mileage",
        "variant": True, "transmission": True, "year_tolerance": 1, "mileage_band_tolerance": 1,
        "description": "Widened to include an adjacent mileage band",
    },
    {
        "key": "drop_variant",
        "variant": False, "transmission": True, "year_tolerance": 1, "mileage_band_tolerance": 1,
        "description": "Dropped variant match — trim naming varies a lot between sources",
    },
    {
        "key": "widen_year",
        "variant": False, "transmission": True, "year_tolerance": 2, "mileage_band_tolerance": 1,
        "description": "Widened year range to ±2",
    },
    {
        "key": "drop_transmission",
        "variant": False, "transmission": False, "year_tolerance": 2, "mileage_band_tolerance": 1,
        "description": "Dropped transmission match",
    },
    {
        "key": "make_model_only",
        "variant": False, "transmission": False, "year_tolerance": None, "mileage_band_tolerance": None,
        "description": "Loosest: matched on make and model only",
    },
]


def _mileage_band_index(mileage):
    if mileage is None:
        return None
    for index in range(len(MILEAGE_BANDS) - 1, -1, -1):
        if mileage >= MILEAGE_BANDS[index]:
            return index
    return 0


def _parse_price(raw_price):
    """VehicleListing.price is a free-text CharField ('$18,500', '18500',
    'POA', ...) — extract a usable number or return None so unparsable/asking
    'Price on application' rows are excluded from the sample rather than
    silently treated as $0."""
    if not raw_price:
        return None
    digits = re.sub(r'[^\d.]', '', str(raw_price))
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_year(raw_year):
    if not raw_year:
        return None
    try:
        return int(str(raw_year).strip()[:4])
    except ValueError:
        return None


def _text_matches(stored, target):
    if not target:
        return True  # criterion not supplied by the caller — don't filter on it
    return bool(stored) and stored.strip().lower() == target.strip().lower()


def _year_matches(stored_year, target_year, tolerance):
    if tolerance is None or target_year is None:
        return True
    parsed = _parse_year(stored_year)
    return parsed is not None and abs(parsed - target_year) <= tolerance


def _mileage_matches(stored_mileage, target_mileage, band_tolerance):
    if band_tolerance is None or target_mileage is None:
        return True
    stored_band = _mileage_band_index(stored_mileage)
    if stored_band is None:
        return False
    target_band = _mileage_band_index(target_mileage)
    return abs(stored_band - target_band) <= band_tolerance


def _listing_matches_tier(listing, criteria, tier):
    vehicle = listing.vehicle
    if not vehicle:
        return False
    if not _text_matches(vehicle.make, criteria["make"]):
        return False
    if not _text_matches(vehicle.model, criteria["model"]):
        return False
    if tier["variant"] and not _text_matches(vehicle.variant, criteria["variant"]):
        return False
    if tier["transmission"] and not _text_matches(vehicle.transmission, criteria["transmission"]):
        return False
    if not _year_matches(vehicle.year, criteria["year"], tier["year_tolerance"]):
        return False
    if not _mileage_matches(vehicle.mileage, criteria["mileage"], tier["mileage_band_tolerance"]):
        return False
    return True


def estimate_price(criteria):
    """
    criteria: dict with make, model (required), variant, transmission
    (optional), year, mileage (required ints/strings).

    Returns a dict describing the estimate (or the lack of one) — see
    estimate_vehicle_price view docstring for the exact response shape.
    """
    # make + model narrowed at the DB level (cheap); everything else needs
    # the band/tolerance logic below so it's checked in Python against this
    # already-small candidate pool rather than in SQL.
    candidate_pool = list(
        VehicleListing.objects
        .select_related('vehicle')
        .filter(
            lifecycle_status=VehicleListing.LIFECYCLE_SOLD,
            vehicle__make__iexact=criteria["make"],
            vehicle__model__iexact=criteria["model"],
        )
    )

    best_price_count = 0
    for tier in WIDENING_TIERS:
        matched = [listing for listing in candidate_pool if _listing_matches_tier(listing, criteria, tier)]
        prices = [price for price in (_parse_price(listing.price) for listing in matched) if price is not None]
        best_price_count = max(best_price_count, len(prices))

        if len(prices) >= MIN_SAMPLE_SIZE:
            median_price, p25, p75 = median_p25_p75(prices)
            return {
                "insufficient_data": False,
                "estimated_price": round(median_price, 2),
                "price_range": {"low": round(p25, 2), "high": round(p75, 2)},
                "sample_size": len(prices),
                "tier": tier["key"],
                "widened": tier["key"] != "exact",
                "message": tier["description"],
            }

    return {
        "insufficient_data": True,
        "estimated_price": None,
        "price_range": None,
        "sample_size": best_price_count,
        "tier": None,
        "widened": True,
        "message": (
            f"Only {best_price_count} matching sold vehicle(s) with a usable price were found, "
            f"even after widening to make and model only — at least {MIN_SAMPLE_SIZE} are needed "
            "for a reliable estimate."
        ),
    }


@api_view(['POST'])
@permission_classes([IsAdminUser])
def estimate_vehicle_price(request):
    """
    POST /api/vehicle-listing/estimate-price/  (admin-only — see module docstring)

    Request body:
    {
        "make": "Toyota",           // required
        "model": "Corolla",         // required
        "variant": "Ascent Sport",  // optional
        "transmission": "Automatic",// optional
        "year": 2020,               // required
        "mileage": 45000            // required, km
    }

    Response body (found):
    {
        "insufficient_data": false,
        "estimated_price": 18250.0,
        "price_range": {"low": 16800.0, "high": 20200.0},
        "sample_size": 8,
        "tier": "drop_variant",
        "widened": true,
        "message": "Dropped variant match — trim naming varies a lot between sources"
    }

    Response body (not enough data): same shape with insufficient_data=true,
    estimated_price/price_range=null, and an explanatory message.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON format in request body'}, status=400)

    make = (data.get('make') or '').strip()
    model = (data.get('model') or '').strip()
    if not make or not model:
        return JsonResponse({'error': 'make and model are required'}, status=400)

    try:
        year = int(data['year'])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'error': 'year is required and must be a whole number'}, status=400)

    try:
        mileage = int(data['mileage'])
        if mileage < 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'error': 'mileage is required and must be a non-negative whole number'}, status=400)

    criteria = {
        'make': make,
        'model': model,
        'variant': (data.get('variant') or '').strip() or None,
        'transmission': (data.get('transmission') or '').strip() or None,
        'year': year,
        'mileage': mileage,
    }

    result = estimate_price(criteria)
    return JsonResponse(result, status=200)
