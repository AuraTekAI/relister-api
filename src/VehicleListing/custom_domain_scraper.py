import logging
import random
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .custom_domain_adapters import resolve_for_url
from .duplicate_matching import find_existing_vehicle
from .image_pipeline import sync_listing_images
from .vehicle_sync import spec_from_result, sync_vehicle_for_listing
from .models import CustomDomainProfileListing, VehicleListing
from .utils import reactivate_listing

logger = logging.getLogger("custom_domain")


def get_custom_domain_listings(profile_url, user):
    """Entry point — kicks off the scrape for a user's custom-domain dealership URL."""
    if not profile_url:
        logger.error("Missing custom domain profile URL")
        return False, "Missing custom domain URL"

    adapter = resolve_for_url(profile_url)
    if adapter is None:
        logger.error(f"Could not resolve adapter for custom domain URL: {profile_url}")
        return False, "Invalid custom domain URL"

    profile_id = adapter.HOST

    try:
        stock_links = adapter.discover_stock_links(profile_url)
        if not stock_links:
            logger.warning(f"No stock links found for {profile_url}")
            return False, "No listings found on the custom domain"

        # Reconcile-cascade guard. The thread function below deletes (or marks
        # `sales=True`) every existing row whose `list_id` isn't in the
        # incoming batch — that's correct when the catalogue genuinely shrank,
        # but catastrophic when the discovery returned a partial result due
        # to a flaky scraper (e.g. the Buckingham/Playwright hydration race
        # that returned 12 instead of 98). If discovery returns a count that
        # is plausibly a regression — less than half of what's already in the
        # DB for this user+host, and the existing count is large enough that
        # halving it can't be explained by a real dealer turnover — refuse to
        # reconcile and surface the anomaly. Operator can investigate and
        # force a re-scrape once discovery is healthy again.
        existing_count = VehicleListing.objects.filter(
            user=user, seller_profile_id=profile_id
        ).count()
        SANITY_MIN_EXISTING = 20  # below this, a 50% drop is plausibly real
        if existing_count >= SANITY_MIN_EXISTING and len(stock_links) < existing_count * 0.5:
            logger.error(
                f"Discovery anomaly for user={user.email} profile={profile_id}: "
                f"adapter returned {len(stock_links)} stock links but DB has "
                f"{existing_count} existing rows. Refusing to reconcile to "
                f"prevent cascade deletion. Investigate the adapter before "
                f"re-running."
            )
            return False, (
                f"Discovery returned {len(stock_links)} links vs "
                f"{existing_count} existing rows — refusing to reconcile"
            )

        instance = CustomDomainProfileListing.objects.filter(
            url=profile_url, user=user, profile_id=profile_id
        ).first()
        if not instance:
            instance = CustomDomainProfileListing.objects.create(
                url=profile_url,
                user=user,
                status="pending",
                profile_id=profile_id,
                domain=adapter.HOST,
                total_listings=len(stock_links),
            )
        instance.total_listings = len(stock_links)
        instance.processed_listings = 0
        instance.status = "processing"
        instance.domain = adapter.HOST
        instance.save()

        thread = threading.Thread(
            target=custom_domain_profile_listings_thread,
            args=(stock_links, instance, user, profile_id, adapter),
        )
        thread.start()
        return True, "Started processing to extract custom domain listings"

    except Exception as exc:
        logger.error(f"Error fetching custom domain listings: {exc}")
        return False, "Error fetching custom domain listings"


def _apply_listing_update(existing, result):
    existing.price = str(result.get("price")) if result.get("price") is not None else existing.price
    # Flag rows with no usable odometer (None/0) so the duplicate-matcher
    # knows mileage can't be used as a tie-breaker for this listing.
    existing.mileage_unavailable = result.get("mileage") in (None, 0)
    existing.description = result.get("description")
    existing.images = result.get("image")
    existing.location = result.get("location")
    existing.is_changed = True
    existing.save()
    # Spec attributes (make/model/year/mileage/...) live ONLY on the
    # canonical Vehicle row — push the refreshed values there. Never raises.
    sync_vehicle_for_listing(existing, spec_from_result(result))
    sync_listing_images(existing, result.get("image"))

def _process_stock_url(stock_url, listing_id, profile_instance, user, profile_id, adapter,
                       all_incoming_list_ids=None):
    """Scrape/update/create a single listing. Returns True if it should count
    toward `processed_listings` (i.e. it exists or was successfully created).

    `all_incoming_list_ids` is the stock-id set of EVERY link discovered in
    this run — passed to find_existing_vehicle so a row whose stock item is
    still live on the showroom is never a merge candidate (see that param's
    docstring). Without it, a dealer's two structurally identical live cars
    (same make/model/year/colour) collapsed into one row, with `list_id`
    flip-flopping between the two live stock ids on every scrape — the DB
    permanently held one row fewer than the showroom per such pair. The
    Gumtree scraper got this fix in #164; this is the same fix for the
    custom-domain path."""
    already_exists = VehicleListing.objects.filter(
        list_id=listing_id, user=user, seller_profile_id=profile_id
    ).first()

    if already_exists:
        logger.info(
            f"Custom domain listing already exists: {already_exists} price={already_exists.price}"
        )
        stale_pending = (
            already_exists.status in ["pending", "failed", "sold"]
            and already_exists.created_at < timezone.now() - timedelta(days=1)
        )
        stale_completed = (
            already_exists.status == "completed"
            and already_exists.listed_on
            and already_exists.listed_on < timezone.now() - timedelta(days=1)
        )
        if stale_pending or stale_completed:
            result = adapter.parse_listing(stock_url)
            if not result:
                logger.error(f"Failed to refetch custom domain listing {listing_id}")
                return True
            price_match = (
                already_exists.price == str(result.get("price"))
                if result.get("price") is not None
                else True
            )
            images_match = set(already_exists.images or []) == set(result.get("image") or [])
            if (
                already_exists.year == result.get("year")
                and already_exists.make == result.get("make")
                and already_exists.model == result.get("model")
                and price_match
                and images_match
                and already_exists.description == result.get("description")
            ):
                logger.info(f"Custom domain listing {listing_id} unchanged — skipping update")
                return True
            logger.info(f"Custom domain listing {listing_id} changed — updating")
            _apply_listing_update(already_exists, result)
        else:
            logger.info(
                f"Custom domain listing {listing_id} not eligible for update (status={already_exists.status})"
            )
        return True

    time.sleep(random.uniform(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
    result = adapter.parse_listing(stock_url)
    if not result:
        logger.error(f"Failed to fetch details for custom domain listing {listing_id} — skipping")
        return False

    # No match on list_id, but that only tells us the SOURCE's id for this
    # stock item is new to us — not that the physical vehicle is. Dealer sites
    # occasionally reissue stock tokens (a re-index, a relist) for a car we
    # already have a row for. Re-check by VIN / structural attributes using
    # the data we just fetched anyway (no extra request), scoped to this one
    # dealer, before deciding this is really a new vehicle.
    matched = find_existing_vehicle(
        VehicleListing.objects.filter(user=user, seller_profile_id=profile_id),
        vin=result.get("vin"),
        make=result.get("make"), model=result.get("model"), variant=result.get("variant"),
        year=result.get("year"), color=result.get("color"), mileage=result.get("mileage"),
        body_type=result.get("body_type"), fuel_type=result.get("fuel_type"),
        transmission=result.get("transmission"),
        # Rows whose stock items are still live on this showroom are not merge
        # candidates — each live car keeps its own row (see the param's
        # docstring in duplicate_matching.py).
        exclude_list_ids=all_incoming_list_ids,
    )
    if matched is not None:
        logger.info(
            f"Custom domain listing {listing_id} matches existing vehicle_listing id={matched.id} "
            f"(list_id changing {matched.list_id!r} -> {listing_id!r}) — updating in place instead of "
            f"creating a duplicate row"
        )
        if matched.status == "sold" or matched.sales:
            reactivate_listing(matched)
        matched.list_id = str(listing_id)
        matched.custom_domain_profile = profile_instance
        _apply_listing_update(matched, result)
        return True

    # Atomic create: a concurrent scrape thread (e.g. cron firing
    # during an in-progress POST scrape) racing on the same listing
    # gets the unique-together constraint to raise IntegrityError;
    # we catch it and apply the freshly-parsed data as an update
    # instead of inserting a duplicate row.
    try:
        with transaction.atomic():
            vehicle_listing = VehicleListing.objects.create(
                user=user,
                custom_domain_profile=profile_instance,
                list_id=listing_id,
                mileage_unavailable=result.get("mileage") in (None, 0),
                price=str(result.get("price")) if result.get("price") is not None else None,
                description=result.get("description"),
                images=result.get("image"),
                url=result.get("url"),
                location=result.get("location"),
                status="pending",
                is_relist=False,
                seller_profile_id=profile_id,
            )
        # Spec attributes (make/model/year/mileage/...) live ONLY on the
        # canonical Vehicle row — create/link it from the parsed spec (VIN or
        # structural reuse within this dealer, new row otherwise).
        sync_vehicle_for_listing(vehicle_listing, spec_from_result(result))
        sync_listing_images(vehicle_listing, result.get("image"))
        logger.info(f"Created custom domain vehicle_listing: {vehicle_listing}")
    except IntegrityError:
        # Another concurrent thread won the create race. The row now
        # exists; apply the data we already fetched as an update.
        logger.info(
            f"Create race lost for listing {listing_id} — another thread created it; applying parsed data as update"
        )
        raced_row = VehicleListing.objects.filter(
            user=user, list_id=listing_id, seller_profile_id=profile_id
        ).first()
        if raced_row is not None:
            _apply_listing_update(raced_row, result)
    return True


# NOTE: an older duplicate definition of _process_stock_url (without the
# find_existing_vehicle dedup branch) used to live here, silently shadowing
# the real one above — Python keeps the LAST definition, so custom-domain
# duplicate prevention was effectively disabled. Removed; do not redefine.


# A failed fetch is overwhelmingly a TRANSIENT problem (a rate limit, a proxy
# hiccup, a page that hadn't finished hydrating) rather than the listing being
# genuinely gone — it was still in this run's discovery, so the showroom had
# it a moment ago. A single quick retry (see carsforsale._render's own 3
# attempts, seconds apart) doesn't give a rate limit or a proxy rotation any
# real time to clear. Spacing whole extra passes 20s apart does. Bounded to 4
# rounds so a genuinely broken/removed-mid-scrape listing can't hang the
# background thread forever — anything still failing after that is logged and
# picked up by the next scheduled sync, same as before.
_MAX_FETCH_ROUNDS = 4
_FETCH_RETRY_DELAY_SECONDS = 20


def custom_domain_profile_listings_thread(stock_links, profile_instance, user, profile_id, adapter):
    logger.info("Starting custom_domain_profile_listings_thread execution")
    # Every stock id discovered in this run, computed UP FRONT: it doubles as
    # the reconcile "seen" set below and as find_existing_vehicle's
    # exclude_list_ids (a car still live on the showroom must never be merged
    # into — computing it lazily inside the loop would leave later links
    # unprotected while the earlier ones are processed). Fixed for the whole
    # run, across every retry round.
    incoming_list_ids = {
        str(lid)
        for lid in (adapter.extract_listing_id(u) for u in stock_links)
        if lid
    }

    def attempt(stock_url, listing_id):
        # One listing's unhandled exception (bad image URL, adapter bug, a
        # transient network error the adapter didn't already catch, etc.)
        # must never kill the thread — this loop has no caller watching it
        # (fire-and-forget from get_custom_domain_listings), so an uncaught
        # exception here would silently truncate every listing after it in
        # `stock_links` for this run. Catch, log, and treat as a failure this
        # round — eligible for the next retry round like any other failure.
        try:
            return _process_stock_url(stock_url, listing_id, profile_instance, user, profile_id, adapter,
                                      all_incoming_list_ids=incoming_list_ids)
        except Exception:
            logger.exception(
                f"Unhandled error processing custom domain listing {listing_id} "
                f"({stock_url}) — will retry"
            )
            return False

    # (url, listing_id) pairs; URLs with no extractable id are skipped up
    # front (unchanged from before) since no amount of retrying fixes a
    # malformed link.
    pending = []
    for stock_url in stock_links:
        listing_id = adapter.extract_listing_id(stock_url)
        if not listing_id:
            logger.warning(f"Skipping URL without listing id: {stock_url}")
            continue
        pending.append((stock_url, listing_id))

    succeeded = 0
    for round_num in range(1, _MAX_FETCH_ROUNDS + 1):
        still_pending = []
        for stock_url, listing_id in pending:
            if attempt(stock_url, listing_id):
                succeeded += 1
            else:
                still_pending.append((stock_url, listing_id))
        pending = still_pending
        if not pending:
            break
        if round_num < _MAX_FETCH_ROUNDS:
            logger.warning(
                f"custom_domain_profile_listings_thread: {len(pending)} of "
                f"{len(stock_links)} discovered listings failed on round "
                f"{round_num}/{_MAX_FETCH_ROUNDS} (profile={profile_id}, "
                f"user={user.email}) — retrying in {_FETCH_RETRY_DELAY_SECONDS}s"
            )
            time.sleep(_FETCH_RETRY_DELAY_SECONDS)

    if pending:
        logger.warning(
            f"custom_domain_profile_listings_thread: {len(pending)} of "
            f"{len(stock_links)} discovered listings still failed after "
            f"{_MAX_FETCH_ROUNDS} rounds (profile={profile_id}, user={user.email}) "
            f"— they remain unscraped and will be retried on the next scheduled "
            f"sync: {[lid for _, lid in pending]}"
        )

    profile_instance.processed_listings = succeeded
    profile_instance.status = "completed"
    profile_instance.save()

    logger.info("Reconciling custom domain listings absent from incoming stock")
    existing_listings = VehicleListing.objects.filter(
        user=user, seller_profile_id=profile_id
    ).exclude(list_id__in=incoming_list_ids)
    for listing in existing_listings:
        if listing.status in ["pending", "failed", "sold"]:
            logger.info(
                f"Deleting absent custom domain listing {listing.list_id} (status={listing.status})"
            )
            listing.delete()
        elif listing.status == "completed":
            listing.sales = True
            listing.save()
        else:
            logger.info(
                f"Custom domain listing {listing.list_id} unknown status {listing.status} — leaving as is"
            )

    logger.info("Completed custom_domain_profile_listings_thread execution")
