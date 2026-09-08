"""
Vehicle sale price estimation (R16-R21): estimates a realistic market price
for a vehicle from the median sale price of similar SOLD listings already in
our database. This is a statistical lookup over real closed sales, not a
prediction model.

Matching starts strict (make, model, variant, transmission, year ±1,
mileage within ±500 km — R17) and widens one criterion at a time, in the order defined
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
from statistics import quantiles

from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from .models import VehicleListing

MIN_SAMPLE_SIZE = 5

# Sale state lives on VehicleListing.status on this branch — utils.mark_listing_sold()
# writes "sold" there (alongside sales/sold_at), and utils.reactivate_listing() moves
# it back to "completed". Only genuinely sold listings may feed an estimate.
SOLD_STATUS = "sold"


def median_p25_p75(values):
    """
    Generic median / 25th / 75th percentile over a list of numbers (ints or
    floats). Returns (median, p25, p75) — all None for an empty list (a median
    of nothing is undefined, not zero); all equal to the single value for a
    one-item list.
    """
    values = [v for v in values if v is not None]
    if not values:
        return None, None, None
    if len(values) == 1:
        return values[0], values[0], values[0]
    # statistics.quantiles(n=4) with the default 'exclusive' method needs at
    # least 2 data points; returns [Q1, Q2(median), Q3].
    q1, q2, q3 = quantiles(values, n=4)
    return q2, q1, q3

# Mileage match window in km. Two cars count as the same mileage when their
# odometers are within this many km of each other — that is the strict tier.
# Kept tight because the odometer moves price sharply; the single widening
# step below opens it to MILEAGE_TOLERANCE_WIDE_KM before any other criterion
# is relaxed.
MILEAGE_TOLERANCE_KM = 500
MILEAGE_TOLERANCE_WIDE_KM = 2_000

# Each step widens exactly one thing relative to the previous tier, ordered
# from most to least price-sensitive criterion (see the module docstring in
# the PR/commit for the reasoning) — make/model are matched unconditionally
# and never appear here.
WIDENING_TIERS = [
    {
        "key": "exact",
        "variant": True, "transmission": True, "year_tolerance": 1,
        "mileage_tolerance_km": MILEAGE_TOLERANCE_KM,
        "description": "Exact match on variant, transmission, year (±1) and mileage (±500 km)",
    },
    {
        "key": "widen_mileage",
        "variant": True, "transmission": True, "year_tolerance": 1,
        "mileage_tolerance_km": MILEAGE_TOLERANCE_WIDE_KM,
        "description": "Widened the mileage window to ±2,000 km",
    },
    {
        "key": "drop_variant",
        "variant": False, "transmission": True, "year_tolerance": 1,
        "mileage_tolerance_km": MILEAGE_TOLERANCE_WIDE_KM,
        "description": "Dropped variant match — trim naming varies a lot between sources",
    },
    {
        "key": "widen_year",
        "variant": False, "transmission": True, "year_tolerance": 2,
        "mileage_tolerance_km": MILEAGE_TOLERANCE_WIDE_KM,
        "description": "Widened year range to ±2",
    },
    {
        "key": "drop_transmission",
        "variant": False, "transmission": False, "year_tolerance": 2,
        "mileage_tolerance_km": MILEAGE_TOLERANCE_WIDE_KM,
        "description": "Dropped transmission match",
    },
    {
        "key": "make_model_only",
        "variant": False, "transmission": False, "year_tolerance": None,
        "mileage_tolerance_km": None,
        "description": "Loosest: matched on make and model only",
    },
]


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


def _mileage_matches(stored_mileage, target_mileage, tolerance_km):
    if tolerance_km is None or target_mileage is None:
        return True
    if stored_mileage is None:
        return False
    return abs(stored_mileage - target_mileage) <= tolerance_km


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
    if not _mileage_matches(vehicle.mileage, criteria["mileage"], tier["mileage_tolerance_km"]):
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
            status=SOLD_STATUS,
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
