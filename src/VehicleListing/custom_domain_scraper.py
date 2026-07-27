import logging
import random
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .custom_domain_adapters import resolve_for_url
from .models import CustomDomainProfileListing, VehicleListing, ListingUrl
from .utils import mark_listing_sold
from .vehicle_matching import get_or_create_vehicle, sync_vehicle_from_result

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
    # Updates the Vehicle row already linked to THIS listing — not a fresh
    # VIN lookup, so no identity-conflict check needed here (see
    # vehicle_matching.get_or_create_vehicle, used only at first-create time).
    sync_vehicle_from_result(existing.vehicle, result)
    existing.price = str(result.get("price")) if result.get("price") is not None else existing.price
    existing.description = result.get("description")
    existing.save()


def custom_domain_profile_listings_thread(stock_links, profile_instance, user, profile_id, adapter):
    logger.info("Starting custom_domain_profile_listings_thread execution")
    count = 0
    incoming_list_ids = set()

    for stock_url in stock_links:
        listing_id = adapter.extract_listing_id(stock_url)
        if not listing_id:
            logger.warning(f"Skipping URL without listing id: {stock_url}")
            continue
        listing_id = str(listing_id)
        incoming_list_ids.add(listing_id)

        already_exists = VehicleListing.objects.filter(
            gumtree_url__listing_id=listing_id, user=user, seller_profile_id=profile_id
        ).first()

        if already_exists:
            count += 1
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
                    continue
                vehicle = already_exists.vehicle
                price_match = (
                    already_exists.price == str(result.get("price"))
                    if result.get("price") is not None
                    else True
                )
                images_match = set(vehicle.images.values_list('image_url', flat=True)) == set(result.get("image") or [])
                if (
                    vehicle.year == result.get("year")
                    and vehicle.make == result.get("make")
                    and vehicle.model == result.get("model")
                    and price_match
                    and images_match
                    and already_exists.description == result.get("description")
                ):
                    logger.info(f"Custom domain listing {listing_id} unchanged — skipping update")
                    continue
                logger.info(f"Custom domain listing {listing_id} changed — updating")
                _apply_listing_update(already_exists, result)
            else:
                logger.info(
                    f"Custom domain listing {listing_id} not eligible for update (status={already_exists.status})"
                )
                continue
        else:
            time.sleep(random.uniform(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
            result = adapter.parse_listing(stock_url)
            if not result:
                logger.error(f"Failed to fetch details for custom domain listing {listing_id} — skipping")
                continue
            count += 1
            # Atomic create: a concurrent scrape thread (e.g. cron firing
            # during an in-progress POST scrape) racing on the same listing
            # gets the ListingUrl(user, listing_id) unique lookup below to
            # find the just-created row; we catch the create race and apply
            # the freshly-parsed data as an update instead of inserting a
            # duplicate row.
            try:
                with transaction.atomic():
                    listing_url, created_url = ListingUrl.objects.get_or_create(
                        user=user, listing_id=listing_id, defaults={"url": result.get("url") or stock_url}
                    )
                    if not created_url:
                        raise IntegrityError("listing_url already exists")
                    # Dedupes by VIN when the adapter provides one (none currently
                    # do, but this keeps behavior consistent with the Gumtree path
                    # and future-proofs adapters that add VIN extraction later) —
                    # see vehicle_matching.get_or_create_vehicle for the matching
                    # and identity-conflict rules.
                    vehicle = get_or_create_vehicle(result)
                    vehicle_listing = VehicleListing.objects.create(
                        user=user,
                        vehicle=vehicle,
                        gumtree_url=listing_url,
                        price=str(result.get("price")) if result.get("price") is not None else None,
                        description=result.get("description"),
                        status="pending",
                        seller_profile_id=profile_id,
                    )
                logger.info(f"Created custom domain vehicle_listing: {vehicle_listing}")
            except IntegrityError:
                # Another concurrent thread won the create race. The row now
                # exists; apply the data we already fetched as an update.
                logger.info(
                    f"Create race lost for listing {listing_id} — another thread created it; applying parsed data as update"
                )
                raced_row = VehicleListing.objects.filter(
                    user=user, gumtree_url__listing_id=listing_id, seller_profile_id=profile_id
                ).first()
                if raced_row is not None:
                    _apply_listing_update(raced_row, result)

    profile_instance.processed_listings = count
    profile_instance.status = "completed"
    profile_instance.save()

    logger.info("Reconciling custom domain listings absent from incoming stock")
    existing_listings = VehicleListing.objects.filter(
        user=user, seller_profile_id=profile_id
    ).exclude(gumtree_url__listing_id__in=incoming_list_ids)
    for listing in existing_listings:
        if listing.status in ["pending", "failed", "sold"]:
            logger.info(
                f"Deleting absent custom domain listing {listing.id} (status={listing.status})"
            )
            listing.delete()
        elif listing.status == "completed":
            mark_listing_sold(listing)
        else:
            logger.info(
                f"Custom domain listing {listing.id} unknown status {listing.status} — leaving as is"
            )

    logger.info("Completed custom_domain_profile_listings_thread execution")
