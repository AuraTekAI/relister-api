"""Auto-discovery of a dealer's suburb/state (and, for custom-domain dealers,
full address) from their custom_domain_url or Gumtree listings.

Called at signup and on profile-edit so the extension never has to prompt the
dealer for a location. Failure is silent and non-blocking — if discovery can't
determine the address (network error, site has no schema.org markup, etc.) the
user is left with null suburb/state and an admin can fill them in manually.
"""
import logging
from collections import Counter

logger = logging.getLogger('accounts')


def discover_and_save_dealer_location(user, custom_domain_url: str | None) -> None:
    """Resolve an adapter for ``custom_domain_url`` and ask it for the dealer's
    suburb/state. Saves the result onto ``user`` (single ``save()`` with
    ``update_fields`` for those two columns). All exceptions are caught — this
    helper must never raise into the registration or profile-update flow.
    """
    if not custom_domain_url:
        return
    try:
        # Imported here so accounts module load doesn't depend on VehicleListing
        # being fully initialised (avoids any app-loading-order surprises).
        from VehicleListing.custom_domain_adapters import resolve_for_url
    except Exception as exc:
        logger.warning(f"dealer-location discovery: adapter import failed: {exc}")
        return

    try:
        adapter = resolve_for_url(custom_domain_url)
    except Exception as exc:
        logger.warning(
            f"dealer-location discovery: resolve_for_url failed for "
            f"{custom_domain_url}: {exc}"
        )
        return
    if adapter is None:
        return

    try:
        result = adapter.discover_dealer_location(custom_domain_url)
    except Exception as exc:
        logger.warning(
            f"dealer-location discovery: adapter call failed for "
            f"{custom_domain_url}: {exc}"
        )
        return

    if not isinstance(result, dict):
        return
    suburb = result.get("suburb")
    state = result.get("state")
    if not (isinstance(suburb, str) and suburb.strip()):
        return
    if not (isinstance(state, str) and state.strip()):
        return

    address = result.get("address")
    update_fields = ["dealership_suburb", "dealership_state"]
    user.dealership_suburb = suburb.strip()
    user.dealership_state = state.strip().upper()
    # dealership_address is now a mandatory field the dealer types in at
    # registration — it's the source of truth. Only fill it from discovery
    # when it's genuinely empty (e.g. an account created before this field
    # existed), never overwrite a value the dealer already provided.
    if isinstance(address, str) and address.strip() and not user.dealership_address:
        user.dealership_address = address.strip()
        update_fields.append("dealership_address")

    try:
        user.save(update_fields=update_fields)
    except Exception as exc:
        logger.warning(
            f"dealer-location discovery: save failed for user {user.pk}: {exc}"
        )
        return
    logger.info(
        f"dealer-location discovery: saved {user.dealership_suburb}, "
        f"{user.dealership_state} for user {user.pk}"
    )


def derive_and_save_gumtree_dealer_location(user) -> None:
    """Best-effort fallback for dealers who only have a Gumtree profile (no
    custom_domain_url): infer suburb/state as the most common per-listing
    location seen across their already-scraped Gumtree listings.

    Gumtree never exposes a dealer's street address — each ad only carries a
    suburb (Gumtree's own design, for lister privacy) — so this can only ever
    populate dealership_suburb/dealership_state. dealership_address is left
    untouched here; only the custom-domain path (discover_and_save_dealer_location)
    can populate a real street address.

    Only fills in suburb/state when BOTH are still empty — a dealer who also
    has a custom_domain_url already has a more precise, addressable result
    from that path, and this must not clobber it on every re-scrape.

    Called once at the end of gumtree_profile_listings_thread, after that
    batch's listings are saved. Must never raise into that thread — this is a
    read of already-persisted data, not part of the scrape/relist logic.
    """
    if user.dealership_suburb or user.dealership_state:
        return
    try:
        # Imported lazily — same reasoning as the import in
        # discover_and_save_dealer_location: avoid load-order coupling
        # between accounts and VehicleListing.
        from VehicleListing.custom_domain_adapters.base import normalize_au_state
        from VehicleListing.models import VehicleListing

        locations = (
            VehicleListing.objects
            .filter(user=user, gumtree_profile__isnull=False)
            .exclude(location__isnull=True)
            .exclude(location='')
            .values_list('location', flat=True)
        )
        counts = Counter(locations)
        if not counts:
            return
        top_location, _ = counts.most_common(1)[0]
        if ',' not in top_location:
            return
        suburb_part, state_part = top_location.rsplit(',', 1)
        suburb = suburb_part.strip()
        state_code = normalize_au_state(state_part.strip())
        if not suburb or not state_code:
            return

        user.dealership_suburb = suburb
        user.dealership_state = state_code
        user.save(update_fields=['dealership_suburb', 'dealership_state'])
        logger.info(
            f"gumtree dealer-location: saved {suburb}, {state_code} for user {user.pk}"
        )
    except Exception as exc:
        logger.warning(
            f"gumtree dealer-location derivation failed for user "
            f"{getattr(user, 'pk', None)}: {exc}"
        )
