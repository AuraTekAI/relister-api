from fastapi import HTTPException
from zenrows import ZenRowsClient
from .models import VehicleListing,GumtreeProfileListing
from .duplicate_matching import find_existing_vehicle
from .image_pipeline import sync_listing_images
from .vehicle_sync import spec_from_result, sync_vehicle_for_listing
import logging
import time
import random
import threading
from django.conf import settings
from bs4 import BeautifulSoup
import re
from .utils import get_full_state_name, mark_listing_sold, reactivate_listing
# from .models import RelistingFacebooklisting
from django.utils import timezone
from datetime import timedelta
# from .facebook_listing import perform_search_and_delete
from .models import FacebookUserCredentials
import xml.etree.ElementTree as ET

logging = logging.getLogger('gumtree')


def build_gumtree_listing_url(listing_id):
    """Build the public Gumtree ad URL for an ad id.

    The init-data VIP endpoint we scrape returns the ad's *fields*, not its web
    address (and the XML fallback shape carries even less), so there is nothing
    in the response to copy into VehicleListing.url. Gumtree's canonical ad
    pages are /s-ad/<suburb>/<category>/<title-slug>/<adId>, but the id-only
    form below resolves to the same ad, which is all we need to store a working
    link back to the source listing (the custom-domain scraper stores the real
    detail-page URL the same way).
    """
    if not listing_id:
        return None
    return f"https://www.gumtree.com.au/s-ad/{listing_id}"


# Stored when a Gumtree ad advertises no stock number at all (most private
# sellers don't) — stock_number is a non-null column, so it always gets a value.
DEFAULT_STOCK_NUMBER = "1"

# Gumtree has no single canonical label for the dealer stock number: it arrives
# in the ad's detail attributes under whichever key the dealer's feed used.
# Listed most-specific first and matched with punctuation/case stripped, so
# "Stock No." and "STOCK NUMBER" both resolve.
_STOCK_NUMBER_LABELS = (
    "dealerstocknumber",
    "dealerstockno",
    "stocknumber",
    "stockno",
    "stockid",
    "stockcode",
    "stock",
)


def extract_stock_number(category_info):
    """Pull the dealer stock number out of a Gumtree ad's detail attributes.

    `category_info` is the {name: value} map built from the ad's categoryInfo
    block (same shape for the JSON and XML response bodies). Returns
    DEFAULT_STOCK_NUMBER when the ad carries no stock number.
    """
    normalized = {}
    for name, value in (category_info or {}).items():
        if not name:
            continue
        # setdefault: if two labels normalize the same, keep the first Gumtree sent.
        normalized.setdefault(re.sub(r"[^a-z0-9]", "", str(name).lower()), value)
    for label in _STOCK_NUMBER_LABELS:
        value = normalized.get(label)
        if value is not None and str(value).strip():
            return str(value).strip()[:255]
    return DEFAULT_STOCK_NUMBER


def _apply_gumtree_update(existing, result):
    """Write freshly-scraped fields from `result` onto an existing
    VehicleListing and persist. Shared by the two "refresh a stale row"
    branches below and by the VIN/structural duplicate-match branch, so a
    relisted-under-a-new-ad-id vehicle is updated identically to a normal
    same-ad refresh."""
    existing.price = str(result.get("price"))
    existing.stock_number = result.get("stock_number") or DEFAULT_STOCK_NUMBER
    existing.mileage_unavailable = result.get("mileage_unavailable", False)
    existing.description = result.get("description")
    existing.images = result.get("image")
    existing.location = result.get("location")
    # Backfill the source URL on refresh too — rows created before this was
    # populated (and rows whose ad id changed on a relist) still have it blank.
    # Guarded so a missing url in `result` never wipes a good stored one.
    if result.get("url"):
        existing.url = result.get("url")
    existing.is_changed = True
    existing.save()
    # Spec attributes (make/model/year/mileage/vin/...) live ONLY on the
    # canonical Vehicle row — push the refreshed values there. Never raises.
    sync_vehicle_for_listing(existing, spec_from_result(result))
    sync_listing_images(existing, result.get("image"))
def extract_seller_id(profile_url):
    """Extract the seller ID from a Facebook Marketplace profile URL."""
    if profile_url.endswith('/'):
        profile_url = profile_url[:-1]
    seller_id = profile_url.split('/')[-1]
    return seller_id

def format_car_description(description):
    """
    Convert raw car description by replacing <br> tags with line breaks/spaces.

    Args:
        description (str): The raw HTML-formatted description.

    Returns:
        str: Clean description with <br> tags replaced by line breaks.
    """
    # Replace all <br> and <br/> tags with line breaks
    description = re.sub(r'(?i)<br\s*/?>', '\n', description)

    # Strip remaining HTML tags (if any)
    soup = BeautifulSoup(description, "html.parser")
    cleaned_text = soup.get_text()

    return cleaned_text.strip()


# def get_listings(url,user,import_url_instance):
#     """Get listings from Gumtree"""
#     logging.info(f"url: {url}")
#     if not settings.ZENROWS_API_KEY:
#         raise HTTPException(status_code=500, detail="ZENROWS_API_KEY is not configured in the environment variables")
#     list_id = extract_seller_id(url)  # Extract the last part of the URL
#     if list_id.isdigit():
#         client = ZenRowsClient(settings.ZENROWS_API_KEY)
#         base_url = f"https://gt-api.gumtree.com.au/web/vip/init-data/{list_id}"

#         try:
#             dict_data = {}
#             response = client.get(base_url)
#             if response.status_code == 402:
#                 logging.error(f"402 response code received: Check your Zenrows API key{response.status_code}")
#                 return None
#             if response.status_code != 200:
#                 logging.info(f"Response status code is not 200: {response}")
#                 return None
#             response_data = response.json()
#             if not response_data:
#                 logging.error(f"Response data is empty: {response}")
#                 return None

#             for current_data in response_data["categoryInfo"]:
#                 logging.info(f"current_data: {current_data}")
#                 dict_data[current_data['name']] = current_data['value']
#             title=response_data["adHeadingData"]["title"]
#             price=int(response_data["adPriceData"]["amount"])
#             seller_id=response_data["adPosterData"]["randomUserId"] 
#             description=response_data["description"]
#             location=response_data["adLocationData"]["suburb"]
#             state=response_data["adLocationData"]["state"]
#             full_state_name=get_full_state_name(state)
#             location=f"{location}, {full_state_name}"
#             body_type=dict_data["Body Type"]
#             fuel_type=dict_data["Fuel Type"]
#             color=dict_data["Colour"]
#             variant=dict_data["Variant"]
#             year=dict_data["Year"]
#             if dict_data["Make, Model"]:
#                 parts = dict_data["Make, Model"].split(" ", 1)  # Split into two parts at the first space
#                 make = parts[0]  # First part
#                 model = ' '.join(title.split(' ')[2:])
#             else:
#                 model=' '.join(title.split(' ')[2:])
#                 make=dict_data["Make"]
#             odo_meter=dict_data["Odometer"]
#             mileage=int(''.join(filter(str.isdigit, odo_meter)))
            
#             # Split description into lines
#             description_lines = description.splitlines()
#             mileage_text = "Mileage: " + str(mileage) + "km"
            
#             # Check if mileage is already in description (case-insensitive)
#             if mileage_text.lower() not in description.lower():
#                 # Insert mileage as the first line
#                 description_lines.insert(0, mileage_text)
                
#                 # Update the description
#                 description = "\n".join(description_lines)
            
#             enhanced_description=format_car_description(description)                
#             transmission=dict_data["Transmission"]
            
#             # Create a new VehicleListing instance
#             vehicle_listing=VehicleListing.objects.create(
#                 user=user,
#                 gumtree_url=import_url_instance,
#                 list_id=list_id,
#                 year=year,
#                 body_type=body_type,
#                 fuel_type=fuel_type,
#                 color=color,
#                 variant=variant,
#                 make=make,
#                 mileage=mileage,
#                 model=model,
#                 price=str(price),
#                 transmission=transmission,
#                 exterior_colour=color,
#                 interior_colour="Other",
#                 description=enhanced_description,
#                 images=[image.get("xlarge") for image in response_data.get("images", [])],
#                 condition="Excellent",
#                 url=url,
#                 location=location,
#                 seller_profile_id=seller_id,
#                 status="pending"
#             )
#             logging.info(f"vehicle_listing: {vehicle_listing}")
#             import_url_instance.status="Completed"
#             import_url_instance.save()
#             return vehicle_listing

#         except Exception as e:
#             error_detail = getattr(e, "response", {}).get("data", str(e))
#             logging.error(f"Error in get_listings: {error_detail}")
#             return None
#     else:
#         logging.error(f"Invalid URL: {url}")
#         return None


def _parse_gumtree_init_data(response):
    """Normalize the init-data VIP endpoint's response body into the same
    JSON-shaped dict the extraction code below expects, regardless of
    whether Gumtree served JSON or XML for this request.

    As of 2026-07, the endpoint started intermittently (now consistently)
    returning an XML body (`<InitVipDataDto>...`) instead of the JSON it
    always used to return, which made every `response.json()` call raise and
    get swallowed by the caller's blanket except — silently dropping every
    listing detail fetch. This tries JSON first (in case Gumtree ever reverts
    or varies per-request) and falls back to parsing the XML shape.
    """
    try:
        return response.json()
    except Exception:
        pass

    root = ET.fromstring(response.text)

    def _text(el, path):
        found = el.find(path) if el is not None else None
        return found.text if found is not None else None

    category_info = [
        {'name': _text(item, 'name'), 'value': _text(item, 'value')}
        for item in root.findall('./categoryInfo/categoryInfo')
    ]
    images = [
        {'xlarge': _text(img, 'xlarge')}
        for img in root.findall('./images/images')
    ]
    return {
        'adHeadingData': {'title': _text(root, './adHeadingData/title')},
        'adPriceData': {'amount': _text(root, './adPriceData/amount')},
        'adLocationData': {
            'suburb': _text(root, './adLocationData/suburb'),
            'state': _text(root, './adLocationData/state'),
        },
        'description': _text(root, './description'),
        'categoryInfo': category_info,
        'images': images,
    }


def get_gumtree_listing_details(listing_id):
    """
    Fetches listing details from the Gumtree API using ZenRowsClient.

    Parameters:
        listing_id (str): The ID of the listing to retrieve.

    Returns:
        dict: A dictionary containing the details of the listing, or None if an error occurs.
    """
    if not settings.ZENROWS_API_KEY:
        logging.error("ZENROWS_API_KEY is not configured in the environment variables")
        return None

    client = ZenRowsClient(settings.ZENROWS_API_KEY)
    base_url = f"https://gt-api.gumtree.com.au/web/vip/init-data/{listing_id}"
    logging.info(f"Fetching listing details from URL: {base_url}")

    try:
        # The Gumtree init-data endpoint serves the same InitVipDataDto as either JSON
        # or XML depending on content negotiation. It recently started defaulting to XML,
        # which broke response.json(). Explicitly ask for JSON to restore the JSON body.
        response = client.get(base_url, headers={"Accept": "application/json"})
        if response.status_code == 402:
            logging.error(f"402 response code received: Check your Zenrows API key{response.status_code}")
            return None
        if response.status_code != 200:
            logging.error(f"Non-200 response code received: {response.status_code}")
            return None

        try:
            # _parse_gumtree_init_data tries JSON first (in case the Accept header
            # above works) and falls back to parsing Gumtree's XML shape — unlike a
            # bare response.json(), it recovers the listing either way instead of
            # giving up when the header is ignored and XML comes back anyway.
            response_data = _parse_gumtree_init_data(response)
        except Exception as parse_exc:
            # Neither JSON nor the expected XML shape — log the start of the body so
            # this is diagnosable instead of being swallowed as a generic decode error.
            logging.error(
                f"Failed to parse response for listing ID {listing_id}: {parse_exc}; "
                f"response body (first 200 chars): {response.text[:200]!r}"
            )
            return None
        if not response_data:
            logging.error("Empty response data received")
            return None

        # Extract and structure data
        category_info = {item['name']: item['value'] for item in response_data.get("categoryInfo", [])}
        # Title may be absent on some listings — fall back to empty string rather than KeyError.
        title = (response_data.get("adHeadingData") or {}).get("title") or ""
        if category_info.get("Make, Model"):
            parts = category_info.get("Make, Model").split(" ", 1)  # Split into two parts at the first space
            make = parts[0]  # First part
            model = ' '.join(title.split(' ')[2:])
        else:
            model=' '.join(title.split(' ')[2:])
            make=category_info.get("Make")
        # Description can be missing — normalise to empty string so .splitlines()/.lower() are safe.
        description = response_data.get("description") or ""

        # Odometer/mileage is optional and not always under the same key. Cars use
        # "Odometer"; campervans/motorhomes report distance under "KMs"; some vehicle
        # types (forklifts, towed caravans, new/demo stock) have no distance reading at
        # all. Previously category_info.get("Odometer") returned None for these and
        # filter(str.isdigit, None) raised "'NoneType' object is not iterable", which
        # crashed the whole listing and skipped it. Parse defensively across the known
        # keys and flag when genuinely unavailable.
        odo_meter = category_info.get("Odometer") or category_info.get("KMs")
        mileage = None
        mileage_unavailable = True
        if odo_meter:
            digits = ''.join(filter(str.isdigit, odo_meter))
            if digits:
                mileage = int(digits)
                mileage_unavailable = False

        # Split description into lines
        description_lines = description.splitlines()
        # Only prepend the mileage line when we actually have a mileage value.
        if mileage is not None:
            mileage_text = "Mileage: " + str(mileage) + "km"
            # Check if mileage is already in description (case-insensitive)
            if mileage_text.lower() not in description.lower():
                # Insert mileage as the first line
                description_lines.insert(0, mileage_text)
                # Update the description
                description = "\n".join(description_lines)

        enhanced_description=format_car_description(description)
        location=response_data.get("adLocationData", {}).get("suburb")
        state=response_data.get("adLocationData", {}).get("state")
        full_state_name=get_full_state_name(state)
        location=f"{location}, {full_state_name}"
        # Price may be missing entirely (None) or arrive as a decimal string from the
        # XML response shape (e.g. "6999.00") — handle both rather than crashing.
        raw_amount = (response_data.get("adPriceData") or {}).get("amount")
        price = int(float(raw_amount)) if raw_amount is not None else None
        listing_details = {
            "title": (response_data.get("adHeadingData") or {}).get("title"),
            "price": price,
            "description": enhanced_description,
            "image": [image.get("xlarge") for image in (response_data.get("images") or [])],
            "location": location,
            "body_type": category_info.get("Body Type"),
            "fuel_type": category_info.get("Fuel Type"),
            "color": category_info.get("Colour"),
            "variant": category_info.get("Variant"),
            "year": category_info.get("Year"),
            "model": model,
            "make": make,
            "mileage": mileage,
            "mileage_unavailable": mileage_unavailable,
            "transmission": category_info.get("Transmission"),
            # 17-character Vehicle Identification Number. Not every dealer
            # fills this in on Gumtree, so it's optional — None when absent.
            "vin": category_info.get("VIN"),
            # Dealer stock number advertised on the ad; "1" when it has none
            # (see extract_stock_number).
            "stock_number": extract_stock_number(category_info),
            # Was hardcoded to "" — every Gumtree listing was created with a blank
            # url column as a result. Build the ad URL from the id we already have.
            "url": build_gumtree_listing_url(listing_id),
        }
        if not listing_details:
            logging.error(f"No listing details found for listing ID: {listing_id}")
            return None

        logging.info(f"Successfully fetched details for listing ID: {listing_id}")
        logging.info(f"listing_details: {listing_details}")
        return listing_details

    except Exception as e:
        logging.error(f"Error fetching details for listing ID {listing_id}: {e}")
        return None


def is_gumtree_listing_active(list_id):
    """
    Live, single-listing existence check against Gumtree — used right before a relist
    action to close the staleness window left by the twice-daily profile re-scrape
    (check_gumtree_profile_relisting_task). Cheap (one ad, not a full profile fetch).

    Returns:
        True  - listing still exists on Gumtree (safe to relist)
        False - listing confirmed gone (404/removed) - caller should mark it sold
        None  - indeterminate (ZenRows/network error, missing API key) - caller should
                NOT change any state and should retry later rather than assume sold
    """
    if not settings.ZENROWS_API_KEY:
        logging.error("ZENROWS_API_KEY is not configured — cannot verify listing status")
        return None
    if not list_id:
        return None

    client = ZenRowsClient(settings.ZENROWS_API_KEY)
    base_url = f"https://gt-api.gumtree.com.au/web/vip/init-data/{list_id}"

    try:
        response = client.get(base_url, headers={"Accept": "application/json"})
    except Exception as e:
        logging.error(f"is_gumtree_listing_active: request failed for listing {list_id}: {e}")
        return None

    if response.status_code == 404:
        return False
    if response.status_code == 402:
        logging.error(f"is_gumtree_listing_active: 402 from ZenRows for listing {list_id} — check API key/quota")
        return None
    if response.status_code != 200:
        logging.warning(f"is_gumtree_listing_active: non-200 ({response.status_code}) for listing {list_id} — treating as indeterminate")
        return None

    try:
        response_data = _parse_gumtree_init_data(response)
    except Exception as parse_exc:
        logging.warning(f"is_gumtree_listing_active: could not parse response for listing {list_id}: {parse_exc} — treating as indeterminate")
        return None

    # An empty/absent body on a 200 response is how Gumtree represents a removed ad
    # for this endpoint (as opposed to a hard 404) — treat it the same as confirmed-gone.
    if not response_data:
        return False

    return True


def get_gumtree_listings(profile_url,user):
    """
    Fetches all listings for a given seller ID.

    Parameters:
        seller_id (str): The seller's ID.

    Returns:
        list: A list of dictionaries containing details of the listings, or None if an error occurs.
    """
    if not settings.ZENROWS_API_KEY:
        logging.error("ZENROWS_API_KEY is not configured in the environment variables")
        return False,"ZENROWS_API_KEY is not configured in the environment variables"
    seller_id = extract_seller_id(profile_url)  # Extract the last part of the URL
    if not seller_id.isdigit():
        logging.error(f"Invalid seller ID: {seller_id}")
        return False,"Invalid seller ID"

    client = ZenRowsClient(settings.ZENROWS_API_KEY)
    base_url = f"https://gt-api.gumtree.com.au/web/user-profile-service/{seller_id}/listings"
    logging.info(f"Fetching all listings for seller ID: {seller_id}")

    # The Gumtree endpoints serve JSON or XML via content negotiation, so explicitly
    # request JSON to avoid response.json() choking on an XML body (as the init-data
    # endpoint started doing). content-type is unreliable (text/plain), so we ask for it.
    json_headers = {"Accept": "application/json"}

    try:
        # Get total count of listings
        initial_url = f"{base_url}?page=0&size=1"
        initial_response = client.get(initial_url, headers=json_headers)
        if initial_response.status_code != 200:
            logging.error(f"seller id is not valid {initial_response.status_code}")
            return False,"Invalid seller ID"

        try:
            initial_data = initial_response.json()
        except ValueError:
            logging.error(
                f"Failed to decode JSON for seller ID {seller_id} (count request); "
                f"response was not JSON (first 200 chars): {initial_response.text[:200]!r}"
            )
            return False,"Invalid response from Gumtree (not JSON)"
        total_count = initial_data.get("totalCount", 0)
        if total_count == 0:
            logging.warning(f"No listings found for seller ID: {seller_id}")
            return False,"No listings found for seller ID"

        # Fetch all listings
        full_url = f"{base_url}?page=0&size={total_count}"
        full_response = client.get(full_url, headers=json_headers)
        if full_response.status_code != 200:
            logging.error(f"seller id is not valid {full_response.status_code}")
            return False,"seller id is not valid"

        try:
            full_data = full_response.json()
        except ValueError:
            logging.error(
                f"Failed to decode JSON for seller ID {seller_id} (listings request); "
                f"response was not JSON (first 200 chars): {full_response.text[:200]!r}"
            )
            return False,"Invalid response from Gumtree (not JSON)"
        listings = full_data.get("profileListingList", [])
        if not listings:
            logging.warning(f"No listings data found for seller ID: {seller_id}")
            return False,"No listings data found for seller ID"
        gumtree_profile_listing_instance = GumtreeProfileListing.objects.filter(url=profile_url,user=user,profile_id=seller_id).first()
        if not gumtree_profile_listing_instance:
            gumtree_profile_listing_instance = GumtreeProfileListing.objects.create(url=profile_url,user=user,status="pending",profile_id=seller_id,total_listings=total_count)
        gumtree_profile_listing_instance.total_listings = total_count
        gumtree_profile_listing_instance.processed_listings = 0
        gumtree_profile_listing_instance.status = "processing"
        gumtree_profile_listing_instance.save()
        logging.info(f"Total listings found for seller ID {seller_id}: {total_count}")
        # Process each listing via threading
        thread = threading.Thread(target=gumtree_profile_listings_thread, args=(listings,gumtree_profile_listing_instance,user,seller_id))
        thread.start()        
        return True,"Started processing to extract listings"

    except Exception as e:
        logging.error(f"Error fetching listings for seller ID {seller_id}: {e}")
        return False,"Error fetching listings for seller ID"

# def update_facebook_listing(already_exists_listing,Updating_listing_data):
#     """Update the Facebook listing"""
#     if already_exists_listing.is_relist:
#         relisting=RelistingFacebooklisting.objects.filter(listing= already_exists_listing,user=already_exists_listing.user,status__in=["completed","failed"],last_relisting_status=False).first()
#         if relisting and relisting.status == "failed":
#             logging.info(f"Updating the failed Relisting  {already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model},  and updating the new relisting details")
#             already_exists_listing.year=Updating_listing_data.get("year")
#             already_exists_listing.make=Updating_listing_data.get("make")
#             already_exists_listing.model=Updating_listing_data.get("model")
#             already_exists_listing.body_type=Updating_listing_data.get("body_type")
#             already_exists_listing.fuel_type=Updating_listing_data.get("fuel_type")
#             already_exists_listing.color=Updating_listing_data.get("color")
#             already_exists_listing.variant=Updating_listing_data.get("variant")
#             already_exists_listing.price=str(Updating_listing_data.get("price"))
#             already_exists_listing.mileage=Updating_listing_data.get("mileage")
#             already_exists_listing.transmission=Updating_listing_data.get("transmission")
#             already_exists_listing.description=Updating_listing_data.get("description")
#             already_exists_listing.images=Updating_listing_data.get("image")
#             already_exists_listing.location=Updating_listing_data.get("location")
#             already_exists_listing.status="completed"
#             already_exists_listing.is_relist=True
#             already_exists_listing.save()
#             logging.info(f"updated the relisting {already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model} details who have old listing details")
#             return True,"updated the relisting {already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model} details who have old listing details"
#         elif relisting and relisting.status == "completed":
#             search_query=f"{already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model}"
#             credentials=FacebookUserCredentials.objects.filter(user=already_exists_listing.user).first()
#             if credentials:
#                 response=perform_search_and_delete(search_query,already_exists_listing.price,timezone.localtime(relisting.relisting_date),credentials.session_cookie)
#                 if response[0] == 1:
#                     already_exists_listing.year=Updating_listing_data.get("year")
#                     already_exists_listing.make=Updating_listing_data.get("make")
#                     already_exists_listing.model=Updating_listing_data.get("model")
#                     already_exists_listing.body_type=Updating_listing_data.get("body_type")
#                     already_exists_listing.fuel_type=Updating_listing_data.get("fuel_type")
#                     already_exists_listing.color=Updating_listing_data.get("color")
#                     already_exists_listing.variant=Updating_listing_data.get("variant")
#                     already_exists_listing.price=str(Updating_listing_data.get("price"))
#                     already_exists_listing.mileage=Updating_listing_data.get("mileage")
#                     already_exists_listing.transmission=Updating_listing_data.get("transmission")
#                     already_exists_listing.description=Updating_listing_data.get("description")
#                     already_exists_listing.images=Updating_listing_data.get("image")
#                     already_exists_listing.location=Updating_listing_data.get("location")
#                     already_exists_listing.status="completed"
#                     already_exists_listing.is_relist=True
#                     already_exists_listing.save()
#                     relisting.status="failed"
#                     relisting.last_relisting_status=False
#                     relisting.save()
#                     logging.info(f"Relisting {search_query}  who have old listing details deleted successfully")
#                     return True,"Relisting {search_query}  who have old listing details deleted successfully"
#                 else:
#                     logging.info(f"Trying to delete the old relisting and relist again using updated details")
#                     logging.info(f"Failed to delete the relisting {search_query} and the response is {response[1]}")
#                     return False,"Failed to delete the relisting and relist again using updated details"
#             else:
#                 logging.error(f"No credentials found for user {already_exists_listing.user.email}")
#                 return False,"No credentials found for user"

#         else:
#             logging.info(f"unknown relsting status {relisting.status} for user {already_exists_listing.user.email} and listing {already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model}")
#             return False,"unknown relsting status"
#     else:
#         search_query=f"{already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model}"
#         credentials=FacebookUserCredentials.objects.filter(user=already_exists_listing.user).first()
#         if credentials:
#             response=perform_search_and_delete(search_query,already_exists_listing.price,timezone.localtime(already_exists_listing.listed_on),credentials.session_cookie)
#             if response[0] == 1:
#                 already_exists_listing.year=Updating_listing_data.get("year")
#                 already_exists_listing.make=Updating_listing_data.get("make")
#                 already_exists_listing.model=Updating_listing_data.get("model")
#                 already_exists_listing.body_type=Updating_listing_data.get("body_type")
#                 already_exists_listing.fuel_type=Updating_listing_data.get("fuel_type")
#                 already_exists_listing.color=Updating_listing_data.get("color")
#                 already_exists_listing.variant=Updating_listing_data.get("variant")
#                 already_exists_listing.price=str(Updating_listing_data.get("price"))
#                 already_exists_listing.mileage=Updating_listing_data.get("mileage")
#                 already_exists_listing.transmission=Updating_listing_data.get("transmission")
#                 already_exists_listing.description=Updating_listing_data.get("description")
#                 already_exists_listing.images=Updating_listing_data.get("image")
#                 already_exists_listing.location=Updating_listing_data.get("location")
#                 already_exists_listing.status="pending"
#                 already_exists_listing.is_relist=False
#                 already_exists_listing.save()
#                 logging.info(f"Listing {search_query}  who have old listing details deleted successfully")
#                 return True,"Listing {search_query}  who have old listing details deleted successfully"
#             else:
#                 logging.info(f"Failed to delete the listing {search_query} and the response is {response[1]}")
#                 return False,"Failed to delete the listing and relist again using updated details"
#         else:
#             logging.error(f"No credentials found for user {already_exists_listing.user.email}")
#             return False,"No credentials found for user"
        

def gumtree_profile_listings_thread(listings, gumtree_profile_listing_instance, user, seller_id):
    logging.info("Starting gumtree_profile_listings_thread execution")
    count = 0
    incoming_list_ids = set()
    # Every ad id in THIS scrape, known upfront. Passed to find_existing_vehicle
    # so rows belonging to ads that are still live on the profile are never
    # merge candidates — merging one live ad into another live ad's row hides a
    # real vehicle and makes the row's list_id flip-flop between the two ads on
    # every scrape. Dedup should only fold an ad into a row whose own ad has
    # VANISHED from the profile (a genuine relist/renewal under a new ad id).
    all_incoming_list_ids = {str(l.get("id")) for l in listings if l.get("id")}
    for current_list in listings:
        listing_id = current_list.get("id")
        if not listing_id:
            logging.warning("Listing ID is missing, skipping entry")
            continue
        incoming_list_ids.add(str(listing_id))
        logging.info(f"Fetching details for listing ID: {listing_id}")
        already_exists = VehicleListing.objects.filter(list_id=listing_id, user=user, seller_profile_id=seller_id).first()
        if already_exists:
            # Count ALL existing listings, regardless of whether we update them.
            # This ensures count matches the total number of valid listings scraped,
            # not just the ones eligible for updating.
            count+=1
            # Backfill the source URL for rows created while it was hardcoded blank.
            # Done here rather than only in _apply_gumtree_update because most
            # branches below skip the update entirely when nothing changed, which
            # would leave those rows blank forever. The URL is derived from the ad
            # id we already have, so this costs no extra request.
            if not already_exists.url:
                already_exists.url = build_gumtree_listing_url(listing_id)
                already_exists.save(update_fields=['url', 'updated_at'])
            logging.info(f"Listing already exists: {already_exists} and price is {already_exists.price}")
            if (already_exists.status in ["pending", "failed","sold"] and already_exists.created_at < timezone.now() - timedelta(days=1)):
                logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status} and already exist title is {already_exists.year} {already_exists.make} {already_exists.model} and price is {already_exists.price} and mileage is {already_exists.mileage} and location is {already_exists.location}")
                result = get_gumtree_listing_details(listing_id)
                logging.info(f"result: {result}")
                if result and already_exists.year == result.get("year") and already_exists.make == result.get("make") and already_exists.model == result.get("model") and already_exists.price == str(result.get("price")) and set(already_exists.images) == set(result.get("image") or []) and already_exists.description == result.get("description"):
                    logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status} and the required details are matched")
                    logging.info(f"No need to update the listing {already_exists.list_id} details")
                    continue
                else:
                    logging.info(f"Listing ID {already_exists.list_id} is already exit but the details are not matching")
                    logging.info(f"update the listing {already_exists.list_id} details")
                    if result:
                        _apply_gumtree_update(already_exists, result)
                        logging.info(f"Updated listing {already_exists.list_id} with new details")
                    else:
                        logging.error(f"Failed to fetch details for updating the listing {listing_id}, skipping update")
                        continue
            elif already_exists.status == "completed" and already_exists.listed_on < timezone.now() - timedelta(days=1):
                logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status}")
                result = get_gumtree_listing_details(listing_id)
                if result and already_exists.year == result.get("year") and already_exists.make == result.get("make") and already_exists.model == result.get("model") and already_exists.price == str(result.get("price")) and set(already_exists.images) == set(result.get("image") or []) and already_exists.description == result.get("description"):
                    logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status} and the required details are matched")
                    logging.info(f"No need to update the listing {already_exists.list_id} details")
                    continue
                else:
                    logging.info(f"Listing ID {already_exists.list_id} is already exit but the details are not matching")
                    logging.info(f"update the listing {already_exists.list_id} details")
                    if result:
                        _apply_gumtree_update(already_exists, result)
                        logging.info(f"Updated listing {already_exists.list_id} with new details")
                    else:
                        logging.error(f"Failed to fetch details for updating the listing {listing_id}, skipping update")
                        continue
            else:
                logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status} and the listing is not eligible for update")
                continue
        else:
            logging.info(f"Listing ID {listing_id} does not exist, fetching details")
            time.sleep(random.uniform(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))

            # Retry failed fetches up to 3 times with exponential backoff
            # This handles race conditions (listing deleted between API calls) and
            # temporary network/API errors.
            result = None
            for attempt in range(3):
                result = get_gumtree_listing_details(listing_id)
                if result:
                    break
                if attempt < 2:  # Don't sleep after the last attempt
                    retry_delay = 2 ** attempt  # 1s, 2s, 4s
                    logging.warning(
                        f"Failed to fetch details for listing ID {listing_id}, "
                        f"attempt {attempt + 1}/3, retrying in {retry_delay}s..."
                    )
                    time.sleep(retry_delay)

            if not result:
                logging.error(
                    f"Failed to fetch details for listing ID {listing_id} after 3 attempts, "
                    f"skipping this listing (may have been deleted on Gumtree)"
                )
                continue

            # No match on this ad id, but that only tells us Gumtree's OWN id for
            # this ad is new to us — not that the physical vehicle is new. Dealers
            # routinely let an ad expire and relist the same car, which Gumtree
            # gives a brand new ad id. Re-check by VIN / structural attributes
            # using the data we just fetched anyway (no extra request), scoped to
            # this one seller, before treating it as a genuinely new vehicle.
            matched = find_existing_vehicle(
                VehicleListing.objects.filter(user=user, seller_profile_id=seller_id),
                vin=result.get("vin"),
                make=result.get("make"), model=result.get("model"), variant=result.get("variant"),
                year=result.get("year"), color=result.get("color"), mileage=result.get("mileage"),
                body_type=result.get("body_type"), fuel_type=result.get("fuel_type"),
                transmission=result.get("transmission"),
                # Rows whose ads are still live on this profile are not merge
                # candidates — each live ad keeps its own row (see the param's
                # docstring in duplicate_matching.py).
                exclude_list_ids=all_incoming_list_ids,
            )
            if matched is not None:
                logging.info(
                    f"Listing ID {listing_id} matches existing vehicle_listing id={matched.id} "
                    f"(ad id changing {matched.list_id!r} -> {listing_id!r}) — updating in place "
                    f"instead of creating a duplicate row"
                )
                count += 1
                if matched.status == "sold" or matched.sales:
                    reactivate_listing(matched)
                matched.list_id = str(listing_id)
                matched.gumtree_profile = gumtree_profile_listing_instance
                _apply_gumtree_update(matched, result)
                continue

            count += 1
            vehicle_listing = VehicleListing.objects.create(
                user=user,
                gumtree_profile=gumtree_profile_listing_instance,
                list_id=listing_id,
                mileage_unavailable=result.get("mileage_unavailable", False),
                price=str(result.get("price")),
                stock_number=result.get("stock_number") or DEFAULT_STOCK_NUMBER,
                description=result.get("description"),
                images=result.get("image"),
                url=result.get("url"),
                location=result.get("location"),
                status="pending",
                is_relist=False,
                seller_profile_id=seller_id
            )
            # Spec attributes (make/model/year/mileage/vin/...) live ONLY on
            # the canonical Vehicle row — create/link it from the parsed spec
            # (VIN or structural reuse within this dealer, new row otherwise).
            sync_vehicle_for_listing(vehicle_listing, spec_from_result(result))
            sync_listing_images(vehicle_listing, result.get("image"))
            logging.info(f"Created new vehicle_listing: {vehicle_listing}")
        # Update GumtreeProfileListing instance with the count of processed listings
    gumtree_profile_listing_instance.processed_listings = count
    gumtree_profile_listing_instance.status = "completed"
    gumtree_profile_listing_instance.save()

    # Listings that came back in this API call are still live on the seller's profile,
    # so clear any stale sales=True flag in bulk.
    existing_listings_which_sale_on_list_api_call = VehicleListing.objects.filter(
        user=user, seller_profile_id=seller_id, list_id__in=incoming_list_ids
    )
    updated_sales_count = existing_listings_which_sale_on_list_api_call.update(sales=False)
    logging.info(f"Reset sales=False on {updated_sales_count} listings still present on the seller's profile")

    # Mark already exist listing who are not present in profile listings as sold.
    # Skip this block if the incoming API call returned no listings — otherwise we'd
    # treat every existing listing as "missing" and wipe them all.
    if not incoming_list_ids:
        logging.warning("Incoming list IDs are empty; skipping missing-listing cleanup to avoid wiping all rows")
    else:
        logging.info("Checking for existing listings not present in incoming listings to mark as sold")
        missing_listings = VehicleListing.objects.filter(
            user=user, seller_profile_id=seller_id
        ).exclude(list_id__in=incoming_list_ids)

        if not missing_listings.exists():
            logging.info("No old listings found which not exist in the profile listings")
        else:
            # Bulk delete pending/failed/sold listings that disappeared from the profile
            deletable = missing_listings.filter(status__in=["pending", "failed", "sold"])
            deleted_count, _ = deletable.delete()
            logging.info(f"Bulk deleted {deleted_count} pending/failed/sold listings missing from profile")

            # Mark completed listings as sold. Goes through mark_listing_sold() (not a bulk
            # .update()) so status flips to "sold" and sold_at is stamped, not just the
            # sales=True flag — the relist-queue endpoint (get_old_vehicle_listings) and the
            # extension's publish guard both need status=="sold" to reliably exclude these,
            # since a stale sales flag alone was previously being missed downstream.
            completed_missing = list(missing_listings.filter(status="completed"))
            for listing in completed_missing:
                mark_listing_sold(listing)
            logging.info(f"Marked {len(completed_missing)} completed listings as sold (status=sold, sales=True)")

    # Best-effort, read-only, non-blocking: back-fill dealership_suburb/state
    # for Gumtree-only dealers from their just-scraped listings. Never
    # touches scrape/relist logic above — see accounts.dealer_location.
    try:
        from accounts.dealer_location import derive_and_save_gumtree_dealer_location
        derive_and_save_gumtree_dealer_location(user)
    except Exception as exc:
        logging.warning(f"gumtree dealer-location hook failed: {exc}")

    logging.info("Completed gumtree_profile_listings_thread execution")