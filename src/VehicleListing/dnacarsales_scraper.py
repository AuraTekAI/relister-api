import logging
import re
import time
import random
import threading
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

from .models import VehicleListing, DNACarSalesProfileListing

logging = logging.getLogger('dnacarsales')

DNA_BASE_URL = "https://www.dnacarsales.com.au"
DNA_STOCK_PATH = "/used-cars-in-wangara/"
DNA_PROFILE_ID = "dnacarsales"  # single dealer — fixed identifier
DNA_LOCATION = "Wangara, WA"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _http_get(url):
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)


def _extract_listing_id(stock_url):
    """Trailing digits in /stock/...-<digits> are the unique listing id."""
    match = re.search(r"-(\d+)$", stock_url.rstrip("/"))
    return match.group(1) if match else None


def _format_description(description):
    if not description:
        return ""
    description = re.sub(r"(?i)<br\s*/?>", "\n", description)
    return BeautifulSoup(description, "html.parser").get_text().strip()


def _parse_price(text):
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _parse_mileage(text):
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _absolute_image_url(src):
    if not src:
        return None
    if src.startswith("http://") or src.startswith("https://"):
        return src
    if src.startswith("/"):
        return f"{DNA_BASE_URL}{src}"
    return f"{DNA_BASE_URL}/{src}"


def get_dnacarsales_stock_links():
    """Walk paginated stock index, return a list of unique /stock/... absolute URLs."""
    links = []
    seen = set()
    page = 1
    while True:
        if page == 1:
            page_url = f"{DNA_BASE_URL}{DNA_STOCK_PATH}"
        else:
            page_url = f"{DNA_BASE_URL}{DNA_STOCK_PATH}pagenum-{page}"
        logging.info(f"Fetching DNA stock index: {page_url}")
        try:
            response = _http_get(page_url)
        except Exception as exc:
            logging.error(f"Failed to fetch {page_url}: {exc}")
            break
        if response.status_code != 200:
            logging.error(f"Non-200 ({response.status_code}) for {page_url}")
            break

        page_links = re.findall(r"""href=['"](/stock/[^'"]+)['"]""", response.text)
        new_count = 0
        for href in page_links:
            if href not in seen:
                seen.add(href)
                links.append(f"{DNA_BASE_URL}{href}")
                new_count += 1

        if new_count == 0:
            # No new entries on this page → end of pagination
            break
        page += 1
        # Defensive cap so a misconfigured site can't loop forever
        if page > 50:
            logging.warning("DNA pagination exceeded 50 pages — stopping")
            break
        time.sleep(random.uniform(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))

    logging.info(f"Total unique DNA stock links collected: {len(links)}")
    return links


def get_dnacarsales_listing_details(stock_url):
    """Fetch a single /stock/... detail page and parse out the VehicleListing-shape dict."""
    listing_id = _extract_listing_id(stock_url)
    if not listing_id:
        logging.error(f"Could not extract listing id from {stock_url}")
        return None
    try:
        response = _http_get(stock_url)
    except Exception as exc:
        logging.error(f"Failed to fetch {stock_url}: {exc}")
        return None
    if response.status_code != 200:
        logging.error(f"Non-200 ({response.status_code}) for {stock_url}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Heading e.g. "2019 TOYOTA LANDCRUISER 4D WAGON LC200 VX (4x4) VDJ200R"
    name_node = soup.select_one("#details-vehicle-info-vehicle-Name")
    full_name = name_node.get_text(strip=True) if name_node else ""
    parts = full_name.split(" ")
    year = parts[0] if len(parts) > 0 else None
    make = parts[1] if len(parts) > 1 else None
    model = parts[2] if len(parts) > 2 else None
    variant = " ".join(parts[3:]) if len(parts) > 3 else None

    price_node = soup.select_one("#details-vehicle-info-vehicle-Price")
    price = _parse_price(price_node.get_text(strip=True)) if price_node else None

    desc_node = soup.select_one("#details-vehicle-info-vehicle-Description")
    description = _format_description(desc_node.decode_contents()) if desc_node else ""

    # Spec table — <tr data-value="X"><td>Label</td><td>Value</td></tr>
    spec = {}
    for row in soup.select("tr[data-value]"):
        key = row.get("data-value")
        cells = row.find_all("td")
        if key and len(cells) >= 2:
            spec[key] = cells[-1].get_text(strip=True)

    body_type = spec.get("Body")
    fuel_type = spec.get("Fuel")
    color = spec.get("Colour")
    transmission = spec.get("Transmission")
    mileage = _parse_mileage(spec.get("Odometer"))

    # Images live inside <ul class="bxslider"> as <img src=...>
    images = []
    for img in soup.select("ul.bxslider img"):
        src = img.get("src")
        absolute = _absolute_image_url(src)
        if absolute and absolute not in images:
            images.append(absolute)

    # Inject mileage line into description (matches Gumtree behaviour for parity)
    if mileage is not None:
        mileage_text = f"Mileage: {mileage}km"
        if mileage_text.lower() not in description.lower():
            description = f"{mileage_text}\n{description}".strip()

    listing_details = {
        "list_id": listing_id,
        "title": full_name,
        "price": price,
        "description": description,
        "image": images,
        "location": DNA_LOCATION,
        "body_type": body_type,
        "fuel_type": fuel_type,
        "color": color,
        "variant": variant,
        "year": year,
        "model": model,
        "make": make,
        "mileage": mileage,
        "transmission": transmission,
        "url": stock_url,
    }
    logging.info(f"Parsed DNA listing {listing_id}: {full_name}")
    return listing_details


def get_dnacarsales_listings(profile_url, user):
    """Entry point — kicks off the scrape for a user's DNA dealership URL."""
    if not profile_url or not profile_url.startswith(DNA_BASE_URL):
        logging.error(f"Invalid DNA profile URL: {profile_url}")
        return False, "Invalid DNA Car Sales URL"

    try:
        stock_links = get_dnacarsales_stock_links()
        if not stock_links:
            logging.warning("No DNA stock links found")
            return False, "No listings found on DNA Car Sales"

        instance = DNACarSalesProfileListing.objects.filter(
            url=profile_url, user=user, profile_id=DNA_PROFILE_ID
        ).first()
        if not instance:
            instance = DNACarSalesProfileListing.objects.create(
                url=profile_url,
                user=user,
                status="pending",
                profile_id=DNA_PROFILE_ID,
                total_listings=len(stock_links),
            )
        instance.total_listings = len(stock_links)
        instance.processed_listings = 0
        instance.status = "processing"
        instance.save()

        thread = threading.Thread(
            target=dnacarsales_profile_listings_thread,
            args=(stock_links, instance, user, DNA_PROFILE_ID),
        )
        thread.start()
        return True, "Started processing to extract DNA Car Sales listings"

    except Exception as exc:
        logging.error(f"Error fetching DNA listings: {exc}")
        return False, "Error fetching DNA Car Sales listings"


def _apply_listing_update(existing, result):
    existing.year = result.get("year")
    existing.make = result.get("make")
    existing.model = result.get("model")
    existing.body_type = result.get("body_type")
    existing.fuel_type = result.get("fuel_type")
    existing.color = result.get("color")
    existing.variant = result.get("variant")
    existing.price = str(result.get("price")) if result.get("price") is not None else existing.price
    existing.mileage = result.get("mileage")
    existing.transmission = result.get("transmission")
    existing.description = result.get("description")
    existing.images = result.get("image")
    existing.location = result.get("location")
    existing.is_changed = True
    existing.save()


def dnacarsales_profile_listings_thread(stock_links, profile_instance, user, profile_id):
    logging.info("Starting dnacarsales_profile_listings_thread execution")
    count = 0
    incoming_list_ids = set()

    for stock_url in stock_links:
        listing_id = _extract_listing_id(stock_url)
        if not listing_id:
            logging.warning(f"Skipping URL without listing id: {stock_url}")
            continue
        incoming_list_ids.add(str(listing_id))

        already_exists = VehicleListing.objects.filter(
            list_id=listing_id, user=user, seller_profile_id=profile_id
        ).first()

        if already_exists:
            count += 1
            logging.info(f"DNA listing already exists: {already_exists} price={already_exists.price}")
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
                result = get_dnacarsales_listing_details(stock_url)
                if not result:
                    logging.error(f"Failed to refetch DNA listing {listing_id}")
                    continue
                price_match = already_exists.price == str(result.get("price")) if result.get("price") is not None else True
                images_match = set(already_exists.images or []) == set(result.get("image") or [])
                if (
                    already_exists.year == result.get("year")
                    and already_exists.make == result.get("make")
                    and already_exists.model == result.get("model")
                    and price_match
                    and images_match
                    and already_exists.description == result.get("description")
                ):
                    logging.info(f"DNA listing {listing_id} unchanged — skipping update")
                    continue
                logging.info(f"DNA listing {listing_id} changed — updating")
                _apply_listing_update(already_exists, result)
            else:
                logging.info(f"DNA listing {listing_id} not eligible for update (status={already_exists.status})")
                continue
        else:
            time.sleep(random.uniform(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
            result = get_dnacarsales_listing_details(stock_url)
            if not result:
                logging.error(f"Failed to fetch details for DNA listing {listing_id} — skipping")
                continue
            count += 1
            vehicle_listing = VehicleListing.objects.create(
                user=user,
                dnacarsales_profile=profile_instance,
                list_id=listing_id,
                year=result.get("year"),
                body_type=result.get("body_type"),
                fuel_type=result.get("fuel_type"),
                color=result.get("color"),
                variant=result.get("variant"),
                make=result.get("make"),
                mileage=result.get("mileage"),
                model=result.get("model"),
                price=str(result.get("price")) if result.get("price") is not None else None,
                transmission=result.get("transmission"),
                description=result.get("description"),
                images=result.get("image"),
                url=result.get("url"),
                location=result.get("location"),
                status="pending",
                is_relist=False,
                seller_profile_id=profile_id,
            )
            logging.info(f"Created DNA vehicle_listing: {vehicle_listing}")

    profile_instance.processed_listings = count
    profile_instance.status = "completed"
    profile_instance.save()

    logging.info("Checking for DNA listings not present in incoming stock to mark as sold")
    existing_listings = VehicleListing.objects.filter(
        user=user, seller_profile_id=profile_id
    ).exclude(list_id__in=incoming_list_ids)
    for listing in existing_listings:
        if listing.status in ["pending", "failed", "sold"]:
            logging.info(f"Deleting absent DNA listing {listing.list_id} (status={listing.status})")
            listing.delete()
        elif listing.status == "completed":
            listing.sales = True
            listing.save()
        else:
            logging.info(f"DNA listing {listing.list_id} unknown status {listing.status} — leaving as is")

    logging.info("Completed dnacarsales_profile_listings_thread execution")
