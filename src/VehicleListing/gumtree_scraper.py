from fastapi import HTTPException
from zenrows import ZenRowsClient
from .models import VehicleListing,GumtreeProfileListing
import logging
import time
import random
import threading
from django.conf import settings
from bs4 import BeautifulSoup
import re
from .utils import get_full_state_name
from .models import RelistingFacebooklisting
from django.utils import timezone
from datetime import timedelta
from .facebook_listing import perform_search_and_delete
from .models import FacebookUserCredentials
from relister.settings import MAX_RETRIES_ATTEMPTS

logging = logging.getLogger('gumtree')
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
            if response.status_code == 402:
                logging.error(f"402 response code received: Check your Zenrows API key{response.status_code}")
                return None
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
            price=int(response_data["adPriceData"]["amount"])
            seller_id=response_data["adPosterData"]["randomUserId"] 
            description=response_data["description"]
            enhanced_description=format_car_description(description)
            location=response_data["adLocationData"]["suburb"]
            state=response_data["adLocationData"]["state"]
            full_state_name=get_full_state_name(state)
            location=f"{location}, {full_state_name}"
            body_type=dict_data["Body Type"]
            fuel_type=dict_data["Fuel Type"]
            color=dict_data["Colour"]
            variant=dict_data["Variant"]
            year=dict_data["Year"]
            if dict_data["Make, Model"]:
                parts = dict_data["Make, Model"].split(" ", 1)  # Split into two parts at the first space
                make = parts[0]  # First part
                model = ' '.join(title.split(' ')[2:])
            else:
                model=' '.join(title.split(' ')[2:])
                make=dict_data["Make"]
            odo_meter=dict_data["Odometer"]
            mileage=int(''.join(filter(str.isdigit, odo_meter)))                
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
                exterior_colour=color,
                interior_colour="Other",
                description=enhanced_description,
                images=[image.get("baseurl") for image in response_data.get("images", [])],
                condition="Excellent",
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
        if response.status_code == 402:
            logging.error(f"402 response code received: Check your Zenrows API key{response.status_code}")
            return None
        if response.status_code != 200:
            logging.error(f"Non-200 response code received: {response.status_code}")
            return None

        response_data = response.json()
        if not response_data:
            logging.error("Empty response data received")
            return None

        # Extract and structure data
        category_info = {item['name']: item['value'] for item in response_data.get("categoryInfo", [])}
        title=response_data["adHeadingData"]["title"]
        if category_info.get("Make, Model"):
            parts = category_info.get("Make, Model").split(" ", 1)  # Split into two parts at the first space
            make = parts[0]  # First part
            model = ' '.join(title.split(' ')[2:])
        else:
            model=' '.join(title.split(' ')[2:])
            make=category_info.get("Make") 
        description=response_data.get("description")
        enhanced_description=format_car_description(description) 
        location=response_data.get("adLocationData", {}).get("suburb")
        state=response_data.get("adLocationData", {}).get("state")
        full_state_name=get_full_state_name(state)
        location=f"{location}, {full_state_name}"
        listing_details = {
            "title": response_data.get("adHeadingData", {}).get("title"),
            "price": int(response_data.get("adPriceData", {}).get("amount")),
            "description": enhanced_description,
            "image": [image.get("baseurl") for image in response_data.get("images", [])],
            "location": location,
            "body_type": category_info.get("Body Type"),
            "fuel_type": category_info.get("Fuel Type"),
            "color": category_info.get("Colour"),
            "variant": category_info.get("Variant"),
            "year": category_info.get("Year"),
            "model": model,
            "make": make,
            "mileage":int(''.join(filter(str.isdigit, category_info.get("Odometer")))) ,
            "transmission": category_info.get("Transmission"),
            "url": ""
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
        gumtree_profile_listing_instance = GumtreeProfileListing.objects.filter(url=profile_url,user=user,profile_id=seller_id).first()
        if not gumtree_profile_listing_instance:
            gumtree_profile_listing_instance = GumtreeProfileListing.objects.create(url=profile_url,user=user,status="pending",profile_id=seller_id,total_listings=total_count)

        thread = threading.Thread(target=gumtree_profile_listings_thread, args=(listings,gumtree_profile_listing_instance,user,seller_id))
        thread.start()        
        return True,"Started processing to extract listings"

    except Exception as e:
        logging.error(f"Error fetching listings for seller ID {seller_id}: {e}")
        return False,"Error fetching listings for seller ID"

def update_facebook_listing(already_exists_listing,Updating_listing_data):
    """Update the Facebook listing"""
    if already_exists_listing.is_relist:
        relisting=RelistingFacebooklisting.objects.filter(listing= already_exists_listing,user=already_exists_listing.user,status__in=["completed","failed"],last_relisting_status=False).first()
        if relisting and relisting.status == "failed":
            already_exists_listing.year=Updating_listing_data.get("year")
            already_exists_listing.make=Updating_listing_data.get("make")
            already_exists_listing.model=Updating_listing_data.get("model")
            already_exists_listing.body_type=Updating_listing_data.get("body_type")
            already_exists_listing.fuel_type=Updating_listing_data.get("fuel_type")
            already_exists_listing.color=Updating_listing_data.get("color")
            already_exists_listing.variant=Updating_listing_data.get("variant")
            already_exists_listing.price=str(Updating_listing_data.get("price"))
            already_exists_listing.mileage=Updating_listing_data.get("mileage")
            already_exists_listing.transmission=Updating_listing_data.get("transmission")
            already_exists_listing.description=Updating_listing_data.get("description")
            already_exists_listing.images=Updating_listing_data.get("image")
            already_exists_listing.location=Updating_listing_data.get("location")
            already_exists_listing.status="completed"
            already_exists_listing.save()
            logging.info(f"updated the relisting {already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model} details who have old listing details")
            return True,"updated the relisting {already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model} details who have old listing details"
        elif relisting and relisting.status == "completed":
            search_query=f"{already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model}"
            credentials=FacebookUserCredentials.objects.filter(user=already_exists_listing.user).first()
            if credentials:
                response=perform_search_and_delete(search_query,already_exists_listing.price,timezone.localtime(already_exists_listing.listed_on),credentials.session_cookie)
                if response[0] == 1:
                    already_exists_listing.year=Updating_listing_data.get("year")
                    already_exists_listing.make=Updating_listing_data.get("make")
                    already_exists_listing.model=Updating_listing_data.get("model")
                    already_exists_listing.body_type=Updating_listing_data.get("body_type")
                    already_exists_listing.fuel_type=Updating_listing_data.get("fuel_type")
                    already_exists_listing.color=Updating_listing_data.get("color")
                    already_exists_listing.variant=Updating_listing_data.get("variant")
                    already_exists_listing.price=str(Updating_listing_data.get("price"))
                    already_exists_listing.mileage=Updating_listing_data.get("mileage")
                    already_exists_listing.transmission=Updating_listing_data.get("transmission")
                    already_exists_listing.description=Updating_listing_data.get("description")
                    already_exists_listing.images=Updating_listing_data.get("image")
                    already_exists_listing.location=Updating_listing_data.get("location")
                    already_exists_listing.status="completed"
                    already_exists_listing.save()
                    relisting.status="failed"
                    relisting.save()
                    logging.info(f"Relisting {search_query}  who have old listing details deleted successfully")
                    return True,"Relisting {search_query}  who have old listing details deleted successfully"
                # elif response[0] == 2:
                #     already_exists_listing.status="sold"
                #     already_exists_listing.save()
                #     relisting.status="completed"
                #     relisting.last_relisting_status=True
                #     relisting.save()
                #     logging.info(f"Relisting {search_query} marked as sold and on Facebook, status updated to sold")
                #     return True,"Relisting {search_query} marked as sold and on Facebook, status updated to sold"
                else:
                    logging.info(f"Trying to delete the old relisting and relist again using updated details")
                    logging.info(f"Failed to delete the relisting {search_query} and the response is {response[1]}")
                    return False,"Failed to delete the relisting and relist again using updated details"
            else:
                logging.error(f"No credentials found for user {already_exists_listing.user.email}")
                return False,"No credentials found for user"

        else:
            logging.info(f"unknown relsting status {relisting.status} for user {already_exists_listing.user.email} and listing {already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model}")
            return False,"unknown relsting status"
    else:
        search_query=f"{already_exists_listing.year} {already_exists_listing.make} {already_exists_listing.model}"
        credentials=FacebookUserCredentials.objects.filter(user=already_exists_listing.user).first()
        if credentials:
            response=perform_search_and_delete(search_query,already_exists_listing.price,timezone.localtime(already_exists_listing.listed_on),credentials.session_cookie)
            if response[0] == 1:
                already_exists_listing.year=Updating_listing_data.get("year")
                already_exists_listing.make=Updating_listing_data.get("make")
                already_exists_listing.model=Updating_listing_data.get("model")
                already_exists_listing.body_type=Updating_listing_data.get("body_type")
                already_exists_listing.fuel_type=Updating_listing_data.get("fuel_type")
                already_exists_listing.color=Updating_listing_data.get("color")
                already_exists_listing.variant=Updating_listing_data.get("variant")
                already_exists_listing.price=str(Updating_listing_data.get("price"))
                already_exists_listing.mileage=Updating_listing_data.get("mileage")
                already_exists_listing.transmission=Updating_listing_data.get("transmission")
                already_exists_listing.description=Updating_listing_data.get("description")
                already_exists_listing.images=Updating_listing_data.get("image")
                already_exists_listing.location=Updating_listing_data.get("location")
                already_exists_listing.status="pending"
                already_exists_listing.save()
                logging.info(f"Listing {search_query}  who have old listing details deleted successfully")
                return True,"Listing {search_query}  who have old listing details deleted successfully"
            # elif response[0] == 2:
            #     already_exists_listing.status="sold"
            #     already_exists_listing.save()
            #     logging.info(f"listing {search_query} marked as sold and on facebook, status updated to sold")
            #     return True,"listing {search_query} marked as sold and on facebook, status updated to sold"
            else:
                logging.info(f"Failed to delete the listing {search_query} and the response is {response[1]}")
                return False,"Failed to delete the listing and relist again using updated details"
        else:
            logging.error(f"No credentials found for user {already_exists_listing.user.email}")
            return False,"No credentials found for user"
        

def gumtree_profile_listings_thread(listings, gumtree_profile_listing_instance, user, seller_id):
    logging.info("Starting gumtree_profile_listings_thread execution")
    count = 0
    incoming_list_ids = set()
    for current_list in listings:
        listing_id = current_list.get("id")
        if not listing_id:
            logging.warning("Listing ID is missing, skipping entry")
            continue
        incoming_list_ids.add(str(listing_id))
        logging.info(f"Fetching details for listing ID: {listing_id}")
        already_exists = VehicleListing.objects.filter(list_id=listing_id, user=user, seller_profile_id=seller_id).first()
        if already_exists:
            count+=1
            logging.info(f"Listing already exists: {already_exists}")
            if (already_exists.status == "pending" or already_exists.status == "failed") and already_exists.created_at < timezone.now() - timedelta(days=7):
                logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status}")
                result = get_gumtree_listing_details(listing_id)
                logging.info(f"update the listing {already_exists.list_id} details")
                if result:
                    already_exists.year = result.get("year")
                    already_exists.make = result.get("make")
                    already_exists.model = result.get("model")
                    already_exists.body_type = result.get("body_type")
                    already_exists.fuel_type = result.get("fuel_type")
                    already_exists.color = result.get("color")
                    already_exists.variant = result.get("variant")
                    already_exists.price = str(result.get("price"))
                    already_exists.mileage = result.get("mileage")
                    already_exists.transmission = result.get("transmission")
                    already_exists.description = result.get("description")
                    already_exists.images = result.get("image")
                    already_exists.location = result.get("location")
                    already_exists.status = "pending"
                    already_exists.save()
                    logging.info(f"Updated listing {already_exists.list_id} with new details")
                else:
                    logging.error(f"Failed to fetch details for listing ID {listing_id}, skipping update")
                    continue
            elif already_exists.status == "completed" and already_exists.listed_on < timezone.now() - timedelta(days=5):
                logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status}")
                result = get_gumtree_listing_details(listing_id)
                if result and already_exists.year == result.get("year") and already_exists.make == result.get("make") and already_exists.model == result.get("model") and already_exists.price == str(result.get("price")) and already_exists.mileage == result.get("mileage") and already_exists.location == result.get("location") and already_exists.description == result.get("description") and already_exists.images == result.get("image"):
                    logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status} and the required details are matched")
                    logging.info(f"No need to update the listing {already_exists.list_id} details")
                    continue
                else:
                    logging.info(f"Listing ID {already_exists.list_id} is already exit but the details are not matching")
                    update_facebook_listing(already_exists, result)
                    logging.info(f"Called update_facebook_listing for listing {already_exists.list_id}")
            else:
                logging.info(f"Listing ID {already_exists.list_id} is already exit and marked as {already_exists.status} and the listing is not eligible for update")
                continue
        else:
            logging.info(f"Listing ID {listing_id} does not exist, fetching details")
            time.sleep(random.uniform(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
            result = get_gumtree_listing_details(listing_id)
            if result and not already_exists:
                count += 1
                vehicle_listing = VehicleListing.objects.create(
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
                logging.info(f"Created new vehicle_listing: {vehicle_listing}")
        # Update GumtreeProfileListing instance with the count of processed listings
    gumtree_profile_listing_instance.processed_listings = count
    gumtree_profile_listing_instance.status = "completed"
    gumtree_profile_listing_instance.save()
    # Mark already exist listing who are not present in profile listings as sold
    logging.info("Checking for existing listings not present in incoming listings to mark as sold")
    existing_listings = VehicleListing.objects.filter(
        user=user, seller_profile_id=seller_id
    ).exclude(list_id__in=incoming_list_ids)
    if existing_listings:
        for listing in existing_listings:
            if listing.status != "completed":
                logging.info(f"Marking listing ID {listing.list_id} as sold (deleting)")
                listing.delete()
                continue
            elif listing.status == "completed":
                search_query = f"{listing.year} {listing.make} {listing.model}"
                price = listing.price
                listed_on = timezone.localtime(listing.listed_on)
                credentials = FacebookUserCredentials.objects.filter(user=listing.user).first()
                if credentials and listing.is_relist:
                    relisting = RelistingFacebooklisting.objects.filter(listing=listing, user=listing.user, status__in=["completed", "failed"], last_relisting_status=False).first()
                    if relisting and relisting.status == "failed":
                        logging.info(f"Relisting status is failed for {search_query}, deleting listing")
                        listing.delete()
                        continue
                    elif relisting and relisting.status == "completed":
                        listed_on = timezone.localtime(relisting.relisting_date)
                        if relisting.listing.retry_count <= MAX_RETRIES_ATTEMPTS:
                            response = perform_search_and_delete(search_query, price, listed_on, credentials.session_cookie)
                            if response[0] == 1:
                                logging.info(f"Deleted relisted Facebook listing for {search_query}")
                                listing.delete()
                            # elif response[0] == 2:
                            #     listing.status = "sold"
                            #     listing.save()
                            #     relisting.status = "completed"
                            #     relisting.last_relisting_status = True
                            #     relisting.save()
                            #     logging.info(f"Relisting {search_query} marked as sold and on Facebook, status updated to sold")
                            else:
                                relisting.listing.retry_count += 1
                                relisting.listing.save()
                                logging.info(f"Failed to delete relisting for {search_query} from facebook marketplace, retry count increased")
                        else:
                            listing.delete()
                            logging.info(f"Failed to delete the relisting {search_query} and completed the retry attempt. System deleted the listing")
                    else:
                        logging.info(f"Unknown relisting status {getattr(relisting, 'status', None)} for user {listing.user.email} and listing {search_query} and last relisting status {getattr(relisting, 'last_relisting_status', None)}")
                        logging.info(f"Deleting the listing ID {listing.list_id}")
                        listing.delete()
                        continue
                elif credentials and not listing.is_relist:
                    if listing.retry_count <= MAX_RETRIES_ATTEMPTS:
                        response = perform_search_and_delete(search_query, price, listed_on, credentials.session_cookie)
                        if response[0] == 1:
                            logging.info(f"Deleted Facebook listing for {search_query}")
                            listing.delete()
                        # elif response[0] == 2:
                        #         listing.status = "sold"
                        #         listing.save()
                        #         logging.info(f"listing {search_query} marked as sold and on facebook, status updated to sold")
                        else:
                            listing.retry_count += 1
                            listing.save()
                            logging.info(f"Failed to delete listing for {search_query} from facebook marketplace, retry count increased")
                    else:
                        logging.info(f"Failed to delete the listing {search_query} and completed the retry attempt. System marked as sold and deleted")
                        listing.delete()
                else:
                    logging.info(f"No credentials found for user {listing.user.email}")
                    continue
            else:
                logging.info(f"Listing ID {listing.list_id} is already exit and marked as {listing.status} and status is unknown")
                continue
    else:
        logging.info("No old listings found which not exist in the profile listings")
    logging.info("Completed gumtree_profile_listings_thread execution")