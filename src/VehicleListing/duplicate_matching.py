import logging
import re

logger = logging.getLogger('duplicate_matching')


def normalize_for_matching(value):
    """Case/whitespace-insensitive comparison key. Never raises on odd input."""
    if not value:
        return ''
    return re.sub(r'\s+', ' ', str(value).strip().upper())


def is_valid_vin(vin):
    """True for something that looks like a real VIN, not scraper placeholder junk.

    Standard VINs are 17 alphanumeric characters. This is deliberately not a
    full ISO 3779 check-digit validation — just enough to reject the common
    placeholder junk seen in scraped data ("N/A", "-", "0000000000000000",
    "TBATBATBATBATBAT") without rejecting a real VIN. Trusting garbage here
    would let two unrelated cars that both scraped an empty/placeholder VIN as
    the same string get merged into one row — the exact false positive this
    whole module exists to avoid.
    """
    if not vin:
        return False
    v = vin.strip().upper()
    if len(v) != 17 or not v.isalnum():
        return False
    if len(set(v)) == 1:
        return False
    return True


def find_existing_vehicle(dealer_queryset, *, vin=None, make=None, model=None,
                           variant=None, year=None, color=None, mileage=None,
                           body_type=None, fuel_type=None, transmission=None,
                           exclude_list_ids=None):
    """Find the VehicleListing (within `dealer_queryset`) that represents the
    SAME physical vehicle as a freshly-scraped listing that did NOT match on
    `list_id` — i.e. the source's own id for this vehicle changed (a Gumtree
    ad renewed/relisted under a new ad id, a dealer site re-indexing stock),
    which is the actual root cause of the duplicate-row bug this closes.

    `dealer_queryset` MUST already be scoped to exactly one (user,
    seller_profile_id) — this never matches across dealers or across users,
    only "is this a car I've already seen from THIS dealer under a different
    listing id".

    Tiered, most to least certain:

      1. VIN — physically unique to one vehicle. Trusted on its own even if
         every other field mismatches (a price drop AND a description
         rewrite AND a relist all at once is normal dealer behaviour).

      2. Structural match — make + model + year (+ variant/color when
         present on both sides) — deliberately excludes price and mileage
         from the key because those legitimately drift for the SAME car
         between scrapes ("Vehicle price changes" is a listed requirement,
         not a reason to treat it as a different vehicle). Requires an
         UNAMBIGUOUS single candidate: a dealer can genuinely have two
         identical white 2013 Mazda3 Neos in stock, and silently merging
         those into one row would overwrite one real car's data with
         another's. When more than one candidate survives the structural
         filter, body_type/fuel_type/transmission/mileage are used only to
         narrow — if that still doesn't leave exactly one, this returns None
         and the caller creates a new row. An occasional extra row is a far
         cheaper mistake than a wrong merge.

    Returns the matched VehicleListing, or None (caller should create new).

    `exclude_list_ids`: ad/stock ids that are ALSO present in the current
    scrape run. A row whose list_id is in this set belongs to an ad that is
    still live on the profile right now — it cannot be "the old ad id for
    this same car", because both ads exist simultaneously. Merging such a row
    would (a) hide a genuinely distinct second vehicle behind one row, and
    (b) make the row's list_id flip-flop between the two live ads on every
    scrape, so only one ad's data ever survives. Rows for currently-live ads
    are therefore never merge candidates: every ad the profile shows right
    now keeps its own row, and merging only happens against rows whose ad has
    disappeared (a genuine relist/renewal).
    """
    # Spec attributes live on the canonical Vehicle row (VehicleListing's
    # duplicated columns were dropped in migration 0056) — every lookup below
    # goes through the listing→vehicle join; the candidate attribute reads
    # (c.variant, c.mileage, ...) resolve through the same relation via the
    # model's read-only delegates, so select_related keeps this one query.
    dealer_queryset = dealer_queryset.select_related('vehicle')

    if exclude_list_ids:
        dealer_queryset = dealer_queryset.exclude(list_id__in=[str(i) for i in exclude_list_ids])

    if is_valid_vin(vin):
        by_vin = dealer_queryset.filter(vehicle__vin__iexact=vin.strip()).first()
        if by_vin:
            return by_vin
        # A valid-looking VIN with no match is still strong evidence this is
        # a genuinely new vehicle — fall through to the structural check
        # anyway rather than short-circuit, in case the VIN was only added to
        # our data on this scrape (some sources omit it intermittently).

    if not (make and model and year):
        return None

    candidates = list(dealer_queryset.filter(
        vehicle__make__iexact=str(make).strip(),
        vehicle__model__iexact=str(model).strip(),
        vehicle__year=str(year).strip(),
    ))
    if not candidates:
        return None

    def soft_matches(field_value, candidate_value):
        # None on either side = "unknown, don't rule it out". Only an actual
        # mismatch between two known values disqualifies a candidate.
        if not field_value or not candidate_value:
            return True
        return normalize_for_matching(field_value) == normalize_for_matching(candidate_value)

    candidates = [
        c for c in candidates
        if soft_matches(variant, c.variant) and soft_matches(color, c.color)
    ]
    if len(candidates) <= 1:
        return candidates[0] if candidates else None

    # Ambiguous on the structural key alone — narrow using fields that are
    # unlikely to change for the same car but plausibly differ between two
    # genuinely distinct cars of the same make/model/year/colour.
    for field_value, attr in ((body_type, 'body_type'), (fuel_type, 'fuel_type'), (transmission, 'transmission')):
        if not field_value:
            continue
        narrowed = [c for c in candidates if soft_matches(field_value, getattr(c, attr))]
        if 1 <= len(narrowed) < len(candidates):
            candidates = narrowed
        if len(candidates) == 1:
            return candidates[0]

    if mileage is not None:
        with_mileage = [c for c in candidates if c.mileage is not None]
        if with_mileage:
            with_mileage.sort(key=lambda c: abs(c.mileage - mileage))
            closest, runner_up = with_mileage[0], (with_mileage[1] if len(with_mileage) > 1 else None)
            if runner_up is None or abs(closest.mileage - mileage) < abs(runner_up.mileage - mileage):
                return closest

    logger.warning(
        "Ambiguous structural match (%d candidates survive) for make=%r model=%r year=%r "
        "variant=%r color=%r — refusing to auto-merge, caller will create a new row",
        len(candidates), make, model, year, variant, color,
    )
    return None
