from fastapi import HTTPException
from zenrows import ZenRowsClient
from .models import VehicleListing
import requests
import os

API_KEY = "4fa8ea3f06670db603cdf3d47d50e0b5346b90e3"
images_folder = "/home/rana-usama/Documents/Relister Project/relister-api/src/static/images"
os.makedirs(images_folder, exist_ok=True)
def get_listings(url,user):
    print(f"url: {url}")
    if not API_KEY:
        raise HTTPException(status_code=500, detail="ZENROWS_API_KEY is not configured in the environment variables")
    list_id = url.split('/')[-1]  # Extract the last part of the URL
    if list_id.isdigit():
        client = ZenRowsClient(API_KEY)
        base_url = f"https://gt-api.gumtree.com.au/web/vip/init-data/{list_id}"

        try:
            dict_data = {}
            response = client.get(base_url)
            response_data = response.json()
            if not response_data:
                return response_data["error"]
            print(len(response_data["categoryInfo"]))
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


            # Download the image and save it locally    
            image_name = os.path.basename(image)
            image_extension = os.path.splitext(image_name)[1] 
            new_image_name = f"{list_id}_image{image_extension}"  
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
                description=description,
                images=local_image_path,
                url=url,
                location=location
            )
            # print(f"vehicle_listing object: {vehicle_listing}")
            return vehicle_listing

        except Exception as e:
            error_detail = getattr(e, "response", {}).get("data", str(e))
            return error_detail