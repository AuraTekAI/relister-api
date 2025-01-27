from fastapi import HTTPException
from zenrows import ZenRowsClient
from .models import VehicleListing
import requests
import os

API_KEY = "4fa8ea3f06670db603cdf3d47d50e0b5346b90e3"

# Define the path to the images folder
images_folder = "/home/stem-digital/Desktop/relister-api/src/static/images"

# Ensure the images folder exists
os.makedirs(images_folder, exist_ok=True)

def get_listings(url):
    
    if not API_KEY:
        raise HTTPException(status_code=500, detail="ZENROWS_API_KEY is not configured in the environment variables")
    list_id = url.split('/')[-1]  # Extract the last part of the URL
    if list_id.isdigit():
        client = ZenRowsClient(API_KEY)
        base_url = f"https://gt-api.gumtree.com.au/web/vip/init-data/{list_id}"

        try:
            response = client.get(base_url)
            response_data = response.json()
            title=response_data["adHeadingData"]["title"]
            price=response_data["adPriceData"]["amount"]
            description=response_data["description"]
            image=response_data["images"][0]["baseurl"]
            location=response_data["adLocationData"]["suburb"]
            body_type=response_data["categoryInfo"][0]["value"]
            fuel_type=response_data["categoryInfo"][7]["value"]
            color=response_data["categoryInfo"][8]["value"]
            variant=response_data["categoryInfo"][9]["value"]
            year=response_data["categoryInfo"][11]["value"]
            model=response_data["categoryInfo"][12]["value"]
            make=response_data["categoryInfo"][5]["value"]
            mileage=response_data["categoryInfo"][6]["value"]


            # Download the image and save it locally    
            image_name = os.path.basename(image)  # Get image name from the URL
            image_extension = os.path.splitext(image_name)[1]  # Extract the image extension
            new_image_name = f"{list_id}_image{image_extension}"  # Construct new image name
            local_image_path = os.path.join(images_folder, new_image_name)

            try:
                # Download the image
                image_response = requests.get(image)
                image_response.raise_for_status()
                with open(local_image_path, "wb") as file:
                    file.write(image_response.content)
            except requests.exceptions.RequestException as e:
                raise HTTPException(status_code=500, detail=f"Error downloading the image: {e}")

            vehicle_listing=VehicleListing.objects.create(
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
                description=description,
                images=local_image_path,
                url=url,
                location=location
            )
            # category=response_data["categoryName"]
            # logging.info(f"CATEGORY : {category}")
            # logging.info(f"TITLE : {title}")
            # logging.info(f"PRICE : {price}")
            # logging.info(f"DESCRIPTION : {description}")
            # logging.info(f"IMAGE_URL : {image}")
            # logging.info(f"LOCATION DETAILS : {location['suburb']}, {location['state']}, {location['postcode']}")
            return vehicle_listing

        except Exception as e:
            error_detail = getattr(e, "response", {}).get("data", str(e))
            return error_detail
            raise HTTPException(status_code=500, detail={
                "error": str(e),
                "details": error_detail
            })

