"""Auto-discovery of a dealer's suburb/state from their custom_domain_url.

Called at signup and on profile-edit so the extension never has to prompt the
dealer for a location. Failure is silent and non-blocking — if discovery can't
determine the address (network error, site has no schema.org markup, etc.) the
user is left with null suburb/state and an admin can fill them in manually.
"""
import logging

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

    user.dealership_suburb = suburb.strip()
    user.dealership_state = state.strip().upper()
    try:
        user.save(update_fields=["dealership_suburb", "dealership_state"])
    except Exception as exc:
        logger.warning(
            f"dealer-location discovery: save failed for user {user.pk}: {exc}"
        )
        return
    logger.info(
        f"dealer-location discovery: saved {user.dealership_suburb}, "
        f"{user.dealership_state} for user {user.pk}"
    )
