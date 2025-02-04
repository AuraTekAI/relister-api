from fastapi import HTTPException
from zenrows import ZenRowsClient
from .models import VehicleListing

API_KEY = "4fa8ea3f06670db603cdf3d47d50e0b5346b90e3"
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
            # print(len(response_data["categoryInfo"]))
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
            category=response_data["categoryName"]

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
                # category=category,
                model=model,
                price=str(price),
                description=description,
                images=image,
                url=url,
                location=location,
                status="pending"
            )
            # print(f"vehicle_listing object: {vehicle_listing}")
            return vehicle_listing

        except Exception as e:
            error_detail = getattr(e, "response", {}).get("data", str(e))
            return error_detail