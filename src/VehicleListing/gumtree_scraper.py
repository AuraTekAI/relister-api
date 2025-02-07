from fastapi import HTTPException
from zenrows import ZenRowsClient
from .models import VehicleListing
import logging



# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
API_KEY = "796fe18ec2b188966a2430dc6e12a966c688d8f0"
def get_listings(url,user):
    logging.info(f"url: {url}")
    if not API_KEY:
        raise HTTPException(status_code=500, detail="ZENROWS_API_KEY is not configured in the environment variables")
    list_id = url.split('/')[-1]  # Extract the last part of the URL
    if list_id.isdigit():
        client = ZenRowsClient(API_KEY)
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
                dict_data[current_data['name']] = current_data['value']
            title=response_data["adHeadingData"]["title"]
            price=response_data["adPriceData"]["amount"]
            description=response_data["description"]
            image=response_data["images"][0]["baseurl"]
            location=response_data["adLocationData"]["suburb"]
            body_type=dict_data["Body Type"]
            fuel_type=dict_data["Fuel Type"]
            color=dict_data["Colour"]
            variant=dict_data["Variant"]
            year=dict_data["Year"]
            model=dict_data["Model"]
            make=dict_data["Make"]
            mileage=dict_data["Odometer"]
            transmission=dict_data["Transmission"]
            

            vehicle_listing=VehicleListing.objects.create(
                user=user,
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
                images=image,
                url=url,
                location=location,
                status="pending"
            )
            logging.info(f"vehicle_listing: {vehicle_listing}")
            return vehicle_listing

        except Exception as e:
            error_detail = getattr(e, "response", {}).get("data", str(e))
            logging.error(f"Error in get_listings: {error_detail}")
            return None
    else:
        logging.error(f"Invalid URL: {url}")
        return None
    
