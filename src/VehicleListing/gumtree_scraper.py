from fastapi import HTTPException
from zenrows import ZenRowsClient
from .models import VehicleListing,GumtreeProfileListing
import logging
import time
import random
import threading
from django.conf import settings

logging = logging.getLogger('gumtree')
def extract_seller_id(profile_url):
    """Extract the seller ID from a Facebook Marketplace profile URL."""
    if profile_url.endswith('/'):
        profile_url = profile_url[:-1]
    seller_id = profile_url.split('/')[-1]
    return seller_id

def get_listings(url,user,import_url_instance):
    """Get listings from Gumtree"""
    logging.info(f"url: {url}")
    if not settings.ZENROWS_API_KEY:
        raise HTTPException(status_code=500, detail="ZENROWS_API_KEY is not configured in the environment variables")
    list_id = extract_seller_id(url)  # Extract the last part of the URL
    if list_id.isdigit():
        client = ZenRowsClient(settings.ZENROWS_API_KEY)
        base_url = f"https://gt-api.gumtree.com.au/web/vip/init-data/{list_id}"

        try:
            dict_data = {}
            response = client.get(base_url)
            if response.status_code != 200:
                logging.info(f"Response status code is not 200: {response}")
                return None
            response_data = response.json()
            if not response_data:
                logging.error(f"Response data is empty: {response}")
                return None

            for current_data in response_data["categoryInfo"]:
                logging.info(f"current_data: {current_data}")
                dict_data[current_data['name']] = current_data['value']
            title=response_data["adHeadingData"]["title"]
            price=response_data["adPriceData"]["amount"]
            seller_id=response_data["adPosterData"]["randomUserId"] 
            description=response_data["description"]
            location=response_data["adLocationData"]["suburb"]
            body_type=dict_data["Body Type"]
            fuel_type=dict_data["Fuel Type"]
            color=dict_data["Colour"]
            variant=dict_data["Variant"]
            year=dict_data["Year"]
            if dict_data["Make, Model"]:
                parts = dict_data["Make, Model"].split(" ", 1)  # Split into two parts at the first space
                make = parts[0]  # First part
                model = parts[1] if len(parts) > 1 else ""  # Remaining part
            else:
                model=dict_data["Model"]
                make=dict_data["Make"]
            mileage=dict_data["Odometer"]
            transmission=dict_data["Transmission"]
            
            # Create a new VehicleListing instance
            vehicle_listing=VehicleListing.objects.create(
                user=user,
                gumtree_url=import_url_instance,
                list_id=list_id,
                year=year,
                body_type=body_type,
                fuel_type=fuel_type,
                color=color,
                variant=variant,
                make=make,
                mileage=mileage,
                model=model,
                price=str(price),
                transmission=transmission,
                description=description,
                images=[image.get("baseurl") for image in response_data.get("images", [])],
                url=url,
                location=location,
                seller_profile_id=seller_id,
                status="pending"
            )
            logging.info(f"vehicle_listing: {vehicle_listing}")
            import_url_instance.status="Completed"
            import_url_instance.save()
            return vehicle_listing

        except Exception as e:
            error_detail = getattr(e, "response", {}).get("data", str(e))
            logging.error(f"Error in get_listings: {error_detail}")
            return None
    else:
        logging.error(f"Invalid URL: {url}")
        return None


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
        response = client.get(base_url)
        if response.status_code != 200:
            logging.error(f"Non-200 response code received: {response.status_code}")
            return None

        response_data = response.json()
        if not response_data:
            logging.error("Empty response data received")
            return None

        # Extract and structure data
        category_info = {item['name']: item['value'] for item in response_data.get("categoryInfo", [])}
        if category_info.get("Make, Model"):
            parts = category_info.get("Make, Model").split(" ", 1)  # Split into two parts at the first space
            make = parts[0]  # First part
            model = parts[1] if len(parts) > 1 else ""  # Remaining part
        else:
            model=category_info.get("Model")
            make=category_info.get("Make")  
        listing_details = {
            "title": response_data.get("adHeadingData", {}).get("title"),
            "price": response_data.get("adPriceData", {}).get("amount"),
            "description": response_data.get("description"),
            "image": [image.get("baseurl") for image in response_data.get("images", [])],
            "location": response_data.get("adLocationData", {}).get("suburb"),
            "body_type": category_info.get("Body Type"),
            "fuel_type": category_info.get("Fuel Type"),
            "color": category_info.get("Colour"),
            "variant": category_info.get("Variant"),
            "year": category_info.get("Year"),
            "model": model,
            "make": make,
            "mileage": category_info.get("Odometer"),
            "transmission": category_info.get("Transmission"),
            "url": ""
        }
        if not listing_details:
            logging.error(f"No listing details found for listing ID: {listing_id}")
            return None

        logging.info(f"Successfully fetched details for listing ID: {listing_id}")
        return listing_details

    except Exception as e:
        logging.error(f"Error fetching details for listing ID {listing_id}: {e}")
        return None


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

    try:
        # Get total count of listings
        initial_url = f"{base_url}?page=0&size=1"
        initial_response = client.get(initial_url)
        if initial_response.status_code != 200:
            logging.error(f"seller id is not valid {initial_response.status_code}")
            return False,"Invalid seller ID"

        initial_data = initial_response.json()
        total_count = initial_data.get("totalCount", 0)
        if total_count == 0:
            logging.warning(f"No listings found for seller ID: {seller_id}")
            return False,"No listings found for seller ID"

        # Fetch all listings
        full_url = f"{base_url}?page=0&size={total_count}"
        full_response = client.get(full_url)
        if full_response.status_code != 200:
            logging.error(f"seller id is not valid {full_response.status_code}")
            return False,"seller id is not valid"

        full_data = full_response.json()
        listings = full_data.get("profileListingList", [])
        if not listings:
            logging.warning(f"No listings data found for seller ID: {seller_id}")
            return False,"No listings data found for seller ID"
        gumtree_profile_listing_instance = GumtreeProfileListing.objects.create(url=profile_url,user=user,status="pending",profile_id=seller_id,total_listings=total_count)

        thread = threading.Thread(target=gumtree_profile_listings_thread, args=(listings,gumtree_profile_listing_instance,user,seller_id))
        thread.start()        
        return True,"Started processing to extract listings"

    except Exception as e:
        logging.error(f"Error fetching listings for seller ID {seller_id}: {e}")
        return False,"Error fetching listings for seller ID"


def gumtree_profile_listings_thread(listings,gumtree_profile_listing_instance,user,seller_id):
    # Collect details for each listing
    count=0
    for current_list in listings:
        listing_id = current_list.get("id")
        if not listing_id:
            logging.warning("Listing ID is missing, skipping entry")
            continue

        logging.info(f"Fetching details for listing ID: {listing_id}")
        time.sleep(random.uniform(1,3))
        result = get_gumtree_listing_details(listing_id)
        already_exists=VehicleListing.objects.filter(list_id=listing_id,user=user).first()
        if already_exists:
            count+=1
            continue
        if result and not already_exists:
            count+=1
            vehicle_listing=VehicleListing.objects.create(
                user=user,
                gumtree_profile=gumtree_profile_listing_instance,
                list_id=listing_id,
                year=result.get("year"),
                body_type=result.get("body_type"),
                fuel_type=result.get("fuel_type"),
                color=result.get("color"),
                variant=result.get("variant"),
                make=result.get("make"),
                mileage=result.get("mileage"),
                model=result.get("model"),
                price=str(result.get("price")),
                transmission=result.get("transmission"),
                description=result.get("description"),
                images=result.get("image"),
                url=result.get("url"),
                location=result.get("location"),
                status="pending",
                seller_profile_id=seller_id
            )
            logging.info(f"vehicle_listing: {vehicle_listing}")
    gumtree_profile_listing_instance.processed_listings=count
    gumtree_profile_listing_instance.status="completed"
    gumtree_profile_listing_instance.save()