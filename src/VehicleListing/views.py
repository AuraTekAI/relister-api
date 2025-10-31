from .gumtree_scraper import get_listings,get_gumtree_listings,format_car_description
from .url_importer import ImportFromUrl
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework import filters
from .serializers import VehicleListingSerializer, ListingUrlSerializer, FacebookUserCredentialsSerializer,FacebookProfileListingSerializer,GumtreeProfileListingSerializer
from accounts.models import User
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookListing,GumtreeProfileListing,FacebookProfileListing,RelistingFacebooklisting
import json
from .facebook_listing import create_marketplace_listing, perform_search_and_delete, get_facebook_profile_listings, extract_facebook_listing_details, image_upload_verification
from .utils import send_status_reminder_email
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import threading
from rest_framework.decorators import api_view, permission_classes
import time
import random   
from datetime import datetime, timedelta
from queue import Queue
from django.conf import settings
from django.utils import timezone
import logging
from relister.settings import MAX_RETRIES_ATTEMPTS
from django.shortcuts import get_object_or_404

logger = logging.getLogger('relister_views')

# dictionary which hold queues for each user
vehicle_listing_user_queues = {}
user_queues = {}
gumtree_profile_user_queues = {}
facebook_profile_user_queues = {}
# worker function to process the vehicle listings
def worker(user_id):
    while True:
        vehicle_listing = user_queues[user_id].get()
        if vehicle_listing is None:
            break
        create_facebook_listing(vehicle_listing)
        time.sleep(random.randint(20, 30))
        image_verification(None,vehicle_listing)
        user_queues[user_id].task_done()

def delete_vehicle_listing_worker(user_id):
    while True:
        search_query,price,listed_on,session_cookie = vehicle_listing_user_queues[user_id].get()
        if search_query is None or price is None or listed_on is None or session_cookie is None:
            break
        for retry in range(3):
            response = perform_search_and_delete(search_query,price,listed_on,session_cookie)
            if response[0] == 1:
                break
            elif response[0] == 6:
                break
            else:
                time.sleep(300,360)
        vehicle_listing_user_queues[user_id].task_done()


def delete_facebook_profile_listing_worker(user_id):
    while True:
        year_make_model_list,session_cookie = facebook_profile_user_queues[user_id].get()
        if year_make_model_list is None or session_cookie is None:
            break
        delete_multiple_vehicle_listings(year_make_model_list,session_cookie)
        facebook_profile_user_queues[user_id].task_done()

def delete_gumtree_profile_listing_worker(user_id):
    while True:
        year_make_model_list,session_cookie = gumtree_profile_user_queues[user_id].get()
        if year_make_model_list is None or session_cookie is None:
            break
        delete_multiple_vehicle_listings(year_make_model_list,session_cookie)
        gumtree_profile_user_queues[user_id].task_done()

@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def import_url_from_gumtree(request):
    """Import URL from Gumtree"""
    if request.method == 'POST':
        data = json.loads(request.body)
        email = data[0].get('email')
        url = data[0].get('url')
        if not email:
            return JsonResponse({'error': 'Email is required'}, status=200)
        
        user = User.objects.filter(email=email).first()
        if not user:
            return JsonResponse({'error': 'User not found'}, status=200)
        
        # Initialize a queue for the user if it doesn't exist
        if user.id not in user_queues:
            user_queues[user.id] = Queue()
            threading.Thread(target=worker, args=(user.id,), daemon=True).start()
        
        import_url = ImportFromUrl(url)
        is_valid, error_message = import_url.validate()

        if not is_valid:
            return JsonResponse({'error': error_message}, status=200)
        list_id = extract_seller_id(url)
        if not list_id or not list_id.isdigit():
            return JsonResponse({'error': 'Invalid seller ID'}, status=200)
        if ListingUrl.objects.filter(url=url,user=user,listing_id=list_id).exists():
            return JsonResponse({'error': 'URL already exists'}, status=200)
        if VehicleListing.objects.filter(user=user,list_id=list_id).exists():              
            return JsonResponse({'error': 'Listing already exists '}, status=200)
        if import_url.print_url_type() == "Facebook Profile" or import_url.print_url_type() == "Gumtree Profile":
            return  JsonResponse({'error': 'This is Facebook Profile Url, Now, Only Process the Gumtree and Facebook single Url'}, status=200)
        credentials = FacebookUserCredentials.objects.filter(user=user).first()
        if not credentials or credentials.session_cookie == {} or not credentials.status:
            if credentials:
                credentials.status = False
                credentials.save()
                send_status_reminder_email(credentials)
            return JsonResponse({'error': 'No facebook credentials found for the user , Please provide the facebook credentials'}, status=200)
        if import_url.print_url_type() == "Facebook":
            # Extract facebook listing data from URL
            current_listing = {}
            current_listing['url'] = url
            current_listing['mileage'] = None
            import_url_instance = ListingUrl.objects.create(url=url, user=user , status='pending')
            response = extract_facebook_listing_details(current_listing,credentials.session_cookie)
            if response and response.get("year") and response.get("make") and response.get("model") and response["images"]:
                enhanced_description=format_car_description(response.get("description"))
                vehicle_listing = VehicleListing.objects.create(
                    user=user,
                    list_id=list_id,
                    gumtree_url=import_url_instance,
                    year=response["year"],
                    body_type="Other",
                    fuel_type=response["fuel_type"],
                    color=None,
                    variant="Other",
                    make=response["make"],
                    mileage=response["driven"] if response["driven"] else current_listing["mileage"],
                    model=response["model"],
                    price=response.get("price"),
                    transmission=response["transmission"],
                    description=enhanced_description,
                    exterior_colour=response["exterior_colour"],
                    interior_colour=response["interior_colour"],
                    condition=response["condition"],
                    images=response["images"],
                    url=current_listing["url"],
                    location=response.get("location"),
                    status="pending",
                    )
                import_url_instance.status = "completed"
                import_url_instance.save()
                credentials.status = True
                credentials.retry_count = 0
                credentials.save()
                user_queues[user.id].put(vehicle_listing)
                user_data = {
                    'url': url,
                    'message': "Extracted data successfully and listing created in facebook is in progress"
                }
                return JsonResponse(user_data, status=200)
            elif not response.get("year") or not response.get("make") or not response.get("model") or not response["images"]:
                import_url_instance.delete()
                return JsonResponse({'error': 'Failed to extract required Facebook listing details. Please verify and save your Facebook login session, check the URL, and try again.'}, status=200)
            else:
                if credentials.retry_count < settings.MAX_RETRIES_ATTEMPTS:
                    credentials.retry_count += 1
                    credentials.save()
                    import_url_instance.delete()
                    return JsonResponse({'error': 'Failed to extract Facebook listing details. Please verify and save your Facebook login session, check the URL, and try again.'}, status=200)
                else:
                    credentials.status = False
                    credentials.save()
                    import_url_instance.delete()
                    return JsonResponse({'error': 'Failed to extract Facebook listing details. Please verify and save your Facebook login session, check the URL, and try again.'}, status=200)
        # Extract Gumtree data from URL
        else:
            import_url_instance = ListingUrl.objects.create(url=url, user=user , status='pending')
            vehicle_listing = get_listings(url,user,import_url_instance)
            if vehicle_listing:
                vls = VehicleListingSerializer(vehicle_listing)
                print(f"vehicle_listing: {vehicle_listing}")
                user_queues[user.id].put(vehicle_listing)
                # Prepare user related listing data
                user_data = {
                    'url': url,
                    'message': "Extracted data successfully and listing created in facebook is in progress"
                }
                return JsonResponse(user_data, status=200)
            else:
                import_url_instance.delete()
                return JsonResponse({'error': 'Failed to extract data from URL, Check the URL, Zenrows API key and try again'}, status=200)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def all_vehicle_listing(request):
    """Get all vehicle listings"""
    if request.method == 'POST':
        data = json.loads(request.body)
        email = data[0].get('email')  # Access first item since you're sending array
        if not email:
            return JsonResponse({'error': 'Email is required'}, status=400)

        user = User.objects.filter(email=email).first()
        print(f"user: {user}")
        all_vehicle_listing = VehicleListing.objects.filter(user=user)
        if all_vehicle_listing:
            return JsonResponse(all_vehicle_listing, status=200)
        else:
            return JsonResponse({'error': 'No vehicle listings found'}, status=200)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

@csrf_exempt
def all_urls(request):
    """Get all URLs"""
    if request.method == 'POST':
        email = request.POST.get('email')
        user = User.objects.get(email=email).first()
        if not user:
            return JsonResponse({'error': 'User not found'}, status=200)
        all_urls = ListingUrl.objects.filter(user=user)
        return JsonResponse(all_urls, status=200)
    return JsonResponse({'error': 'Invalid request method'}, status=200)
class ListingUrlViewSet(ModelViewSet):
    """Get all URLs"""
    queryset = ListingUrl.objects.all()
    serializer_class = ListingUrlSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['url']
    filterset_fields = ['url']
    ordering_fields = ['url']
    ordering = ['url']

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return ListingUrl.objects.all()
        else:
            return ListingUrl.objects.filter(user=user)
        
    def destroy(self, request, *args, **kwargs):
        """Delete listing URL"""
        instance = self.get_object()
        import_url = ListingUrl.objects.filter(id=instance.id).first()  
        vehicle_listing = VehicleListing.objects.filter(gumtree_url=import_url).first()
        if vehicle_listing:
            search_query = vehicle_listing.year + " " + vehicle_listing.make + " " + vehicle_listing.model
            credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
            if vehicle_listing.status in ["pending", "failed", "sold"]:
                import_url.delete()
                vehicle_listing.delete()
                return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
            else:
                if credentials and credentials.session_cookie and credentials.status:
                    if vehicle_listing.listed_on and not vehicle_listing.is_relist:
                        listed_on = timezone.localtime(vehicle_listing.listed_on)
                    else:
                        relisting=RelistingFacebooklisting.objects.filter(listing=vehicle_listing,status="completed",last_relisting_status=False).first()
                        if relisting:
                            listed_on = timezone.localtime(relisting.relisting_date)
                        else:
                            listed_on = timezone.localtime(timezone.now())
                    price = vehicle_listing.price
                    user_id = vehicle_listing.user.id
                    if user_id not in vehicle_listing_user_queues:
                        vehicle_listing_user_queues[user_id] = Queue()
                        threading.Thread(target=delete_vehicle_listing_worker, args=(user_id,), daemon=True).start()
                    vehicle_listing_user_queues[user_id].put((search_query, price, listed_on, credentials.session_cookie))
                    import_url.delete()
                    vehicle_listing.delete()
                    return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
                else:
                    if credentials:
                        credentials.status = False
                        credentials.save()
                        send_status_reminder_email(credentials)
                    import_url.delete()
                    vehicle_listing.delete()
                    return JsonResponse({'error': 'No facebook credentials found for the user'}, status=200)
        else:
            import_url.delete()
            return JsonResponse({'message': 'Listing URL deleted successfully but vehicle listing not found'}, status=200)

class VehicleListingViewSet(ModelViewSet):
    """Get all vehicle listings with relisting dates"""
    queryset = VehicleListing.objects.all().order_by('-updated_at')
    serializer_class = VehicleListingSerializer  # Use the updated serializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    filterset_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    ordering_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    ordering = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    
    def get_queryset(self):
        """Get all vehicle listings with relisting dates"""
        user = self.request.user
        if user.is_superuser:
            return VehicleListing.objects.all().order_by('-updated_at')
        else:
            return VehicleListing.objects.filter(user=user).order_by('-updated_at')

    def destroy(self, request, *args, **kwargs):
        """Delete vehicle listing"""
        instance = self.get_object()
        print(f"instance: {instance}")
        # Proceed with deletion
        vehicle_listing=VehicleListing.objects.filter(id=instance.id).first()
        search_query = vehicle_listing.year + " " + vehicle_listing.make + " " + vehicle_listing.model
        credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
        if vehicle_listing.status== "pending" or vehicle_listing.status== "failed" or vehicle_listing.status== "sold":
            vehicle_listing.delete()
            return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
        else:
            if credentials and credentials.session_cookie != {} and credentials.status:
                if vehicle_listing.listed_on and not vehicle_listing.is_relist:
                    listed_on = timezone.localtime(vehicle_listing.listed_on)
                else:
                    relisting=RelistingFacebooklisting.objects.filter(listing=vehicle_listing,status="completed",last_relisting_status=False).first()
                    if relisting:
                        listed_on = timezone.localtime(relisting.relisting_date)
                    else:
                        listed_on = timezone.localtime(vehicle_listing.listed_on)
                price = vehicle_listing.price
                user_id = vehicle_listing.user.id
                if user_id not in vehicle_listing_user_queues:
                    vehicle_listing_user_queues[user_id] = Queue()
                    threading.Thread(target=delete_vehicle_listing_worker, args=(user_id,), daemon=True).start()
                vehicle_listing_user_queues[user_id].put((search_query,price,listed_on,credentials.session_cookie))
                vehicle_listing.delete()
                return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
            else:
                if credentials:
                    credentials.status = False
                    credentials.save()
                    send_status_reminder_email(credentials)
                vehicle_listing.delete()
                return JsonResponse({'error': 'No facebook credentials found for the user'}, status=200)

class FacebookUserCredentialsViewSet(ModelViewSet):
    """Get all Facebook user credentials"""
    queryset = FacebookUserCredentials.objects.all()
    serializer_class = FacebookUserCredentialsSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['email']
    filterset_fields = ['email']
    ordering_fields = ['email']
    ordering = ['email']

    def get_queryset(self):
        """Get all Facebook user credentials"""
        user = self.request.user
        if user.is_superuser:
            return FacebookUserCredentials.objects.all()
        else:
            return FacebookUserCredentials.objects.filter(user=user)

    def create(self, request, *args, **kwargs):
        """Create Facebook user credentials"""
        data = json.loads(request.body)
        user_id = data.get('user')
        cookies = data.get('cookies')
        origins = data.get('origins')
        user = User.objects.filter(id=user_id).first()
        if not user:
            return JsonResponse({'error': 'User not found'}, status=200)
        enhanced_cookies = []
        if cookies:
            excluded_cookies = {"dbln", "ps_l", "ps_n", "ar_debug"}
            lax_cookies = {"wd", "presence"}

            for cookie in cookies:
                if cookie["name"] in excluded_cookies:
                    continue  # Skip excluded cookies
                same_site_value = "Lax" if cookie["name"] in lax_cookies else "None"
                enhanced_cookie = {
                    "name": cookie["name"],
                    "path": cookie["path"],
                    "value": cookie["value"],
                    "domain": cookie["domain"],
                    "secure": cookie["secure"],
                    "httpOnly": cookie["httpOnly"],
                    "sameSite": same_site_value
                }

                if "expires" in cookie:
                    enhanced_cookie["expires"] = cookie["expires"]

                enhanced_cookies.append(enhanced_cookie)
        session_cookie = {
            "cookies": enhanced_cookies,
            "origins": origins
        }
        already_exist_session_cookie = FacebookUserCredentials.objects.filter(user=user).first()
        if already_exist_session_cookie:
            already_exist_session_cookie.session_cookie = session_cookie
            already_exist_session_cookie.status = True
            already_exist_session_cookie.status_reminder = False
            already_exist_session_cookie.retry_count = 0
            already_exist_session_cookie.save()
            return JsonResponse({'message': 'Facebook user credentials updated successfully'}, status=200)
        else:
            FacebookUserCredentials.objects.create(user=user, session_cookie=session_cookie,status=True,retry_count=0,status_reminder=False)
            return JsonResponse({'message': 'Facebook user credentials created successfully'}, status=200)

def create_facebook_listing(vehicle_listing):
    """Create Facebook listing"""
    try:
        user = vehicle_listing.user
        credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
        if credentials and credentials.session_cookie != {} and credentials.status:
            already_listed = FacebookListing.objects.filter(user=vehicle_listing.user, listing=vehicle_listing,).first()
            if not already_listed:
                time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                current_user=User.objects.filter(id=user.id).first()
                last_day_time = timezone.now() - timedelta(hours=24)
                if current_user.last_facebook_listing_time and current_user.last_facebook_listing_time > last_day_time:
                    current_user.daily_listing_count = 0
                    current_user.save()
                if current_user.daily_listing_count >= settings.MAX_DAILY_LISTINGS_COUNT:
                    vehicle_listing.status="failed"
                    vehicle_listing.save()
                    return False, "Daily listing count limit reached"
                logger.info(f"Creating listing for the user {user.email} and listing title: {vehicle_listing.year} {vehicle_listing.make} {vehicle_listing.model}")
                listing_created, message = create_marketplace_listing(vehicle_listing, credentials.session_cookie)
                if listing_created:
                    FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="success")
                    credentials.status = True
                    credentials.retry_count = 0
                    credentials.save()
                    vehicle_listing.status="completed"
                    vehicle_listing.listed_on=timezone.now()
                    vehicle_listing.updated_at=timezone.now()
                    vehicle_listing.save()
                    current_user.last_facebook_listing_time = timezone.now()
                    current_user.daily_listing_count += 1
                    current_user.save()
                    return True, "Listing created successfully"
                else:
                    if credentials.retry_count < settings.MAX_RETRIES_ATTEMPTS:
                        credentials.retry_count += 1
                        credentials.save()
                        vehicle_listing.status="failed"
                        vehicle_listing.save()
                        return False, "Failed to create listing"
                    else:
                        credentials.status = False
                        credentials.save()
                        vehicle_listing.status="failed"
                        vehicle_listing.save()
                        return False, "Failed to create listing"
            else:
                return True, "Listing already exists"
        else:
            if credentials:
                credentials.status = False
                credentials.save()
                send_status_reminder_email(credentials)
            vehicle_listing.status="failed"
            vehicle_listing.save()
            return False, "No facebook credentials found for the user"
             
    except Exception as e:
        vehicle_listing.status="failed"
        vehicle_listing.save()
        return False, "Failed to create listing"
    

@api_view(['POST'])
@permission_classes([IsAuthenticated])    
def get_gumtree_profile_listings(request):
    """Get Gumtree profile listings"""
    try:
        data = json.loads(request.body)
        profile_url = data.get('gumtree_profile_url')
        email = data.get('email')
        user = User.objects.filter(email=email).first()
        import_url = ImportFromUrl(profile_url)
        is_valid, error_message = import_url.validate()
        if not is_valid:
            return JsonResponse({'error': error_message}, status=200)
        if import_url.print_url_type == "Facebook" or import_url.print_url_type == "Facebook Profile":
            return  JsonResponse({'error': 'Please provide the Gumtree Profile Url'}, status=200)
        seller_id = extract_seller_id(profile_url)
        if not seller_id or not seller_id.isdigit():
            return JsonResponse({'error': 'Invalid seller ID'}, status=200)
        if GumtreeProfileListing.objects.filter(url=profile_url,user=user,profile_id=seller_id).exists():
            return JsonResponse({'error': 'This URL is already processed'}, status=200)
        credentials = FacebookUserCredentials.objects.filter(user=user).first()
        if not credentials or credentials.session_cookie == {}:
            if credentials:
                credentials.status = False
                credentials.save()
                send_status_reminder_email(credentials)
            return JsonResponse({'error': 'No facebook credentials found for the user , Please provide the facebook credentials'}, status=200)
        # Get listings from Gumtree
        success, message = get_gumtree_listings(profile_url, user)
        if success:
            return JsonResponse({'message': message}, status=200)
        else:
            return JsonResponse({'error': message}, status=200)

    except Exception as e:
        return JsonResponse({'message': str(e)}, status=500)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def facebook_profile_listings(request):
    """Get Facebook profile listings"""
    try:
        data = json.loads(request.body)
        profile_url = data.get('profile_url')
        email = data.get('email')
        user = User.objects.filter(email=email).first()
        if not profile_url:
            return JsonResponse({'error': 'Profile URL is required'}, status=400)
        import_url = ImportFromUrl(profile_url)
        is_valid, error_message = import_url.validate()

        if not is_valid:
            return JsonResponse({'error': error_message}, status=200)
        if import_url.print_url_type() == "Gumtree" or import_url.print_url_type() == "Gumtree Profile" or import_url.print_url_type() == "Facebook":
            return  JsonResponse({'error': 'Please provide the Facebook Profile Url'}, status=200)
        seller_id = extract_seller_id(profile_url)
        if not seller_id or not seller_id.isdigit():
            return JsonResponse({'error': 'Invalid seller ID'}, status=200)
        if FacebookProfileListing.objects.filter(url=profile_url,user=user,profile_id=seller_id).exists():
                return JsonResponse({'error': 'This URL is already processed'}, status=200)
        # Get user's Facebook credentials
        credentials = FacebookUserCredentials.objects.filter(user=user).first()
        if not credentials or credentials.session_cookie == {} or not credentials.status:
            if credentials:
                credentials.status = False
                credentials.save()
                send_status_reminder_email(credentials)
            return JsonResponse({'error': 'Facebook credentials not found'}, status=404)
        logger.info(f"start getting listings for the user {user.email} and profile id: {seller_id}")
        success, listings = get_facebook_profile_listings(profile_url, credentials.session_cookie)

        if success:
            credentials.status = True
            credentials.retry_count=0
            credentials.save()
            logger.info(f"Creating facebook profile listing for the user {user.email} and profile id: {seller_id}")
            logger.info(f"Total listings: {len(listings)}")
            facebook_profile_listing_instance = FacebookProfileListing.objects.create(url=profile_url,user=user,status="pending",profile_id=seller_id,total_listings=len(listings))
            # Create a new thread to process the listings
            thread = threading.Thread(target=facebook_profile_listings_thread, args=(listings, credentials,user,seller_id,facebook_profile_listing_instance))
            thread.start()
            return JsonResponse({'message': 'Profile Listings are being processed'}, status=200)
        else:
            if credentials.retry_count < settings.MAX_RETRIES_ATTEMPTS:
                credentials.retry_count += 1
                credentials.save()
                return JsonResponse({'error': 'Failed to get listings'}, status=200)
            else:
                credentials.status = False
                credentials.save()
                return JsonResponse({'error': 'Failed to get listings'}, status=200)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    

def facebook_profile_listings_thread(listings, credentials,user,seller_id,facebook_profile_listing_instance):
    """Get listings using the function"""
    logger.info(f"Getting listings for the user {user.email} and profile id: {seller_id}")
    count=0
    incoming_list_ids = set()
    logger.info(f"Total listings to process: {len(listings)}")
    for current_listing_key in listings:
        current_listing=listings[current_listing_key]
        logger.info(f"Processing listing: {current_listing}")
        already_listed = VehicleListing.objects.filter(user=user, list_id=current_listing["id"]).first()
        incoming_list_ids.add(str(current_listing["id"]))
        if already_listed and already_listed.status == "sold":
            count+=1
            logger.info(f"Vehicle listing already exists and marked as sold for the user {user.email} and listing title: {already_listed.year} {already_listed.make} {already_listed.model} and profile id: {seller_id}")
            # If the listing is already listed, delete it from the FacebookProfileListing
            already_listed.delete()
            continue
        elif already_listed:
            count+=1
            logger.info(f"Vehicle listing already exists for the user {user.email} and listing title: {already_listed.year} {already_listed.make} {already_listed.model} and profile id: {seller_id}")
            continue
        else:
            time.sleep(random.uniform(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
            # Get listings using the function
            vehicleListing=extract_facebook_listing_details(current_listing, credentials.session_cookie)
            if vehicleListing:
                logger.info(f"Vehicle listing found for the user {user.email} and listing title: {vehicleListing.get('year')} {vehicleListing.get('make')} {vehicleListing.get('model')} and profile id: {seller_id}")
                count+=1
                enhanced_description=format_car_description(vehicleListing.get("description"))
                VehicleListing.objects.create(
                    user=user,
                    facebook_profile=facebook_profile_listing_instance,
                    list_id=current_listing["id"],
                    year=vehicleListing["year"],
                    body_type="Other",
                    fuel_type=vehicleListing["fuel_type"],
                    color=None,
                    variant="Other",
                    make=vehicleListing["make"],
                    mileage=vehicleListing["driven"] if vehicleListing["driven"] else current_listing["mileage"],
                    model=vehicleListing["model"],
                    price=vehicleListing.get("price"),
                    transmission=vehicleListing["transmission"],
                    condition=vehicleListing["condition"],
                    description=enhanced_description,
                    images=vehicleListing["images"],
                    url=current_listing["url"],
                    location=vehicleListing.get("location"),
                    status="pending",
                    exterior_colour=vehicleListing["exterior_colour"],
                    interior_colour=vehicleListing["interior_colour"],
                    seller_profile_id=seller_id
                )
            else:
                logger.error(f"Failed to extract Facebook listing details for the user {user.email} and profile id: {seller_id}")
                if credentials.retry_count < settings.MAX_RETRIES_ATTEMPTS:
                    credentials.retry_count += 1
                    credentials.save()
                    continue
                else:
                    credentials.status = False
                    credentials.save()
                    logger.error(f"Failed to extract Facebook listing details for the user {user.email} and profile id: {seller_id}, Max retries reached")
                    break
    facebook_profile_listing_instance.processed_listings = count
    facebook_profile_listing_instance.status = "completed"
    facebook_profile_listing_instance.save()
    #Mark missing listings as SOLD
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
                        time.sleep(random.uniform(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                        response = perform_search_and_delete(search_query, price, listed_on, credentials.session_cookie)
                        if response[0] == 1:
                            logging.info(f"Deleted relisted Facebook listing for {search_query}")
                            listing.delete()
                        elif response[0] == 6:
                            logger.info("Listing not found for relisting and listing title is {listing.year} {listing.make} {listing.model} for the user {user.email}")
                            logger.info(f"response[1]: {response[1]}")
                            listing.delete()
                        elif response[0] == 4:
                            logger.info(f"Got issue inside the automation for deleting the relisting {listing.year} {listing.make} {listing.model} and for user {user.email} ")
                            logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                            logger.info(f"response[1]: {response[1]}")
                            logger.info(f"number of retries: {listing.retry_count}")
                            if listing.retry_count <= MAX_RETRIES_ATTEMPTS:
                                listing.retry_count += 1
                                listing.save()
                                logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} and number of retries: {listing.retry_count}")
                            else:
                                #send email to user related listing
                                logger.info(f"Failed to found the listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} using this automation after all attempts. Please check your listing manually.")
                                logger.info(f"response[1]: {response[1]}")
                                logger.info(f"number of retries: {listing.retry_count}")
                                logger.info(f"delete the listing andlisting ID {listing.list_id}")
                                listing.delete()
                                # send_email_to_user(user, listing)
                                #------------------------------------------------------------------------
                                #---------------------------------------------------------------------------
                                #---------------------------------------------------------------------------
                        elif response[0] == 0:
                            logger.info(f"Failed to load the facebook page for listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                            logger.info(f"response[1]: {response[1]}")
                            if credentials.retry_count < MAX_RETRIES_ATTEMPTS:
                                credentials.retry_count += 1
                                credentials.save()
                                logger.info(f"Retrying relisting for user {user.email} (Attempt {credentials.retry_count})")
                            else:
                                credentials.status = False
                                credentials.save()
                                logger.warning(f"Max retry attempts reached. Credentials disabled for user {user.email}")
                        else:
                            logger.error(f"Relisting failed for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                            logger.info(f"response[1]: {response[1]}")
                        continue
                    else:
                        logging.info(f"Unknown relisting status {getattr(relisting, 'status', None)} for user {listing.user.email} and listing {search_query} and last relisting status {getattr(relisting, 'last_relisting_status', None)}")
                        logging.info(f"Deleting the listing ID {listing.list_id}")
                        continue
                elif credentials and not listing.is_relist:
                    time.sleep(random.uniform(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                    response = perform_search_and_delete(search_query, price, listed_on, credentials.session_cookie)
                    if response[0] == 1:
                        logging.info(f"Deleted relisted Facebook listing for {search_query}")
                        listing.delete()
                    elif response[0] == 6:
                        logger.info("Listing not found for relisting and listing title is {listing.year} {listing.make} {listing.model} for the user {user.email}")
                        logger.info(f"response[1]: {response[1]}")
                        listing.delete()
                    elif response[0] == 4:
                        #email to me and hussain bhai
                        logger.info(f"Got issue inside the automation for deleting the relisting {listing.year} {listing.make} {listing.model} and for user {user.email} ")
                        logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                        logger.info(f"response[1]: {response[1]}")
                        logger.info(f"number of retries: {listing.retry_count}")
                        if listing.retry_count <= MAX_RETRIES_ATTEMPTS:
                            listing.retry_count += 1
                            listing.save()
                            logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} and number of retries: {listing.retry_count}")
                        else:
                            #send email to user related listing
                            logger.info(f"Failed to found the listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} using this automation after all attempts. Please check your listing manually.")
                            logger.info(f"response[1]: {response[1]}")
                            logger.info(f"number of retries: {listing.retry_count}")
                            logger.info(f"delete the listing andlisting ID {listing.list_id}")
                            listing.delete()
                            # send_email_to_user(user, listing)
                            #------------------------------------------------------------------------
                            #---------------------------------------------------------------------------
                            #---------------------------------------------------------------------------
                    elif response[0] == 0:
                        logger.info(f"Failed to load the facebook page for listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                        logger.info(f"response[1]: {response[1]}")
                        if credentials.retry_count < MAX_RETRIES_ATTEMPTS:
                            credentials.retry_count += 1
                            credentials.save()
                            logger.info(f"Retrying relisting for user {user.email} (Attempt {credentials.retry_count})")
                        else:
                            credentials.status = False
                            credentials.save()
                            logger.warning(f"Max retry attempts reached. Credentials disabled for user {user.email}")
                    else:
                        logger.error(f"Relisting failed for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                        logger.info(f"response[1]: {response[1]}")
                    continue
                else:
                    logging.info(f"No credentials found for user {listing.user.email}")
                    continue
            else:
                logging.info(f"Listing ID {listing.list_id} is already exit and marked as {listing.status} and not deleting and status of listing is unknown")
                continue
    else:
        logging.info("No existing listings found that no exist in the incoming list IDs")
    logging.info("Completed facebook_profile_listings_thread execution")

class FacebookProfileListingViewSet(ModelViewSet):
    """Get all Facebook profile listings"""
    queryset = FacebookProfileListing.objects.all()
    serializer_class = FacebookProfileListingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['url']
    filterset_fields = ['url']
    ordering_fields = ['url']
    ordering = ['url']

    def get_queryset(self):
        """Get all Facebook profile listings"""
        user = self.request.user
        if user.is_superuser:
            return FacebookProfileListing.objects.all() 
        else:
            return FacebookProfileListing.objects.filter(user=user).all()
    def destroy(self, request, *args, **kwargs):
        """Delete Facebook profile listing"""
        instance = self.get_object()
        year_make_model_list=[]
        session_cookie=None
        # Proceed with deletion
        facebook_profile_listing=FacebookProfileListing.objects.filter(id=instance.id).first()
        logger.info(f"Deleting Facebook profile listing for the user {facebook_profile_listing.user.email} and profile id: {facebook_profile_listing.profile_id}")
        facebook_profile_vehicle_listings=VehicleListing.objects.filter(facebook_profile=facebook_profile_listing,status="completed").all()
        if facebook_profile_vehicle_listings:
            user = facebook_profile_listing.user
            credentials = FacebookUserCredentials.objects.filter(user=user).first()
            if not credentials or credentials.session_cookie == {} or not credentials.status:
                if credentials:
                    credentials.status = False
                    credentials.save()
                    send_status_reminder_email(credentials)
                return JsonResponse({'error': 'Facebook credentials not found, please login again'}, status=404)
            session_cookie = credentials.session_cookie
            for current_listing in facebook_profile_vehicle_listings:
                temp_list=[]
                temp_list.append(current_listing.year + " " + current_listing.make + " " + current_listing.model)
                temp_list.append(current_listing.price)
                if current_listing.listed_on and not current_listing.is_relist:
                    listed_on = timezone.localtime(current_listing.listed_on)
                else:
                    relisting=RelistingFacebooklisting.objects.filter(listing=current_listing,status="completed",last_relisting_status=False).first()
                    if relisting:
                        listed_on = timezone.localtime(relisting.relisting_date)
                    else:
                        listed_on = timezone.localtime(current_listing.listed_on)
                temp_list.append(listed_on)
                logger.info(f"Adding to year make model temporary list for the user {user.email} and listing title: {current_listing.year} {current_listing.make} {current_listing.model} and price: {current_listing.price} and listed on: {listed_on} which is used to delete the listings")
                year_make_model_list.append(temp_list)
        facebook_profile_listing.delete()
        if year_make_model_list:
            # Initialize a queue for the user if it doesn't exist
            if user.id not in facebook_profile_user_queues:
                facebook_profile_user_queues[user.id] = Queue()
                threading.Thread(target=delete_facebook_profile_listing_worker, args=(user.id,), daemon=True).start()
            facebook_profile_user_queues[user.id].put((year_make_model_list,session_cookie))
        return JsonResponse({'message': 'Listing deleted successfully'}, status=200)

class GumtreeProfileListingViewSet(ModelViewSet):
    """Get all Gumtree profile listings"""
    queryset = GumtreeProfileListing.objects.all()
    serializer_class = GumtreeProfileListingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['url']
    filterset_fields = ['url']
    ordering_fields = ['url']
    ordering = ['url']

    def get_queryset(self):
        """Get all Gumtree profile listings"""
        user = self.request.user
        if user.is_superuser:
            return GumtreeProfileListing.objects.all()
        else:
           
            return GumtreeProfileListing.objects.filter(user=user).all()
    def destroy(self, request, *args, **kwargs):
        """Delete Gumtree profile listing"""
        instance = self.get_object()
        year_make_model_list=[]
        session_cookie=None
        # Proceed with deletion
        gumtree_profile_listing=GumtreeProfileListing.objects.filter(id=instance.id).first()
        logger.info(f"Deleting Gumtree profile listing for the user {gumtree_profile_listing.user.email} and profile id: {gumtree_profile_listing.profile_id}")
        gumtree_profile_vehicle_listings=VehicleListing.objects.filter(gumtree_profile=gumtree_profile_listing,status="completed").all()
        if gumtree_profile_vehicle_listings:
            user = gumtree_profile_listing.user
            credentials = FacebookUserCredentials.objects.filter(user=user).first()
            if not credentials or credentials.session_cookie == {} or not credentials.status:
                if credentials:
                    credentials.status = False
                    credentials.save()
                    send_status_reminder_email(credentials)
                return JsonResponse({'error': 'Facebook credentials not found, please login again'}, status=404)
            session_cookie = credentials.session_cookie
            for current_listing in gumtree_profile_vehicle_listings:
                temp_list=[]
                temp_list.append(current_listing.year + " " + current_listing.make + " " + current_listing.model)
                temp_list.append(current_listing.price)
                if current_listing.listed_on and not current_listing.is_relist:
                    listed_on = timezone.localtime(current_listing.listed_on)
                else:
                    relisting=RelistingFacebooklisting.objects.filter(listing=current_listing,status="completed",last_relisting_status=False).first()
                    if relisting:
                        listed_on = timezone.localtime(relisting.relisting_date)
                    else:
                        listed_on = timezone.localtime(current_listing.listed_on)
                temp_list.append(listed_on)
                logger.info(f"Adding to year make model temporary list for the user {user.email} and listing title: {current_listing.year} {current_listing.make} {current_listing.model} and price: {current_listing.price} and listed on: {listed_on} which is used to delete the listings")
                year_make_model_list.append(temp_list)
        gumtree_profile_listing.delete()
        if year_make_model_list:
            # Initialize a queue for the user if it doesn't exist
            if user.id not in gumtree_profile_user_queues:
                gumtree_profile_user_queues[user.id] = Queue()
                threading.Thread(target=delete_gumtree_profile_listing_worker, args=(user.id,), daemon=True).start()
            gumtree_profile_user_queues[user.id].put((year_make_model_list,session_cookie))
        return JsonResponse({'message': 'Listing deleted successfully'}, status=200)

def extract_seller_id(profile_url):
    """Extract the seller ID from a Facebook Marketplace profile URL."""
    if profile_url.endswith('/'):
        profile_url = profile_url[:-1]
    seller_id = profile_url.split('/')[-1]
    return seller_id

def delete_multiple_vehicle_listings(year_make_model_list,session_cookie):
    """Delete multiple vehicle listings"""
    if session_cookie:
        while year_make_model_list:
            current_listing=year_make_model_list.pop(0)
            search_query = current_listing[0]
            price=current_listing[1]
            listing_date=current_listing[2]
            for i in range(3):
                result=perform_search_and_delete(search_query,price,listing_date,session_cookie)
                if result[0] == 1:
                    time.sleep(random.uniform(300,360))
                    break
                elif result[0] == 6:
                    time.sleep(random.uniform(300, 360))
                    break
                else:
                    time.sleep(random.uniform(300, 360))
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_montly_listings_report(request):
    """Get monthly listings report"""
    if request.method == 'GET':
        user = request.user
        pending_vehicle_listings_count = 0
        failed_vehicle_listings_count = 0
        completed_vehicle_listings_count = 0
        pending_gumtree_profile_count = 0
        failed_gumtree_profile_count = 0
        completed_gumtree_profile_count = 0
        pending_facebook_profile_count = 0
        failed_facebook_profile_count = 0
        completed_facebook_profile_count = 0
        total_vehicle_listings_count = 0
        total_gumtree_profile_count = 0
        total_facebook_profile_count = 0
        current_date = datetime.now().date()
        thirty_days_ago = current_date - timedelta(days=30)
        if user.is_superuser:
            vehicle_listings = VehicleListing.objects.filter(updated_at__date__gte=thirty_days_ago).all()
            gumtree_profile_listings = GumtreeProfileListing.objects.filter(updated_at__date__gte=thirty_days_ago).all()
            facebook_profile_listings = FacebookProfileListing.objects.filter(updated_at__date__gte=thirty_days_ago).all()
        else:
            vehicle_listings = VehicleListing.objects.filter(user=user,updated_at__date__gte=thirty_days_ago).all()
            gumtree_profile_listings = GumtreeProfileListing.objects.filter(user=user,updated_at__date__gte=thirty_days_ago).all()
            facebook_profile_listings = FacebookProfileListing.objects.filter(user=user,updated_at__date__gte=thirty_days_ago).all()
        if vehicle_listings:
            for current_listing in vehicle_listings:
                if current_listing.status == "completed":
                    completed_vehicle_listings_count += 1
                elif current_listing.status == "failed":
                    failed_vehicle_listings_count += 1
                else:
                    pending_vehicle_listings_count += 1
            total_vehicle_listings_count = completed_vehicle_listings_count + failed_vehicle_listings_count + pending_vehicle_listings_count
        if gumtree_profile_listings:
            for current_listing in gumtree_profile_listings:
                if current_listing.status == "completed":
                    completed_gumtree_profile_count += 1
                elif current_listing.status == "failed":
                    failed_gumtree_profile_count += 1
                else:
                    pending_gumtree_profile_count += 1
            total_gumtree_profile_count = completed_gumtree_profile_count + failed_gumtree_profile_count + pending_gumtree_profile_count
        if facebook_profile_listings:
            for current_listing in facebook_profile_listings:
                if current_listing.status == "completed":
                    completed_facebook_profile_count += 1
                elif current_listing.status == "failed":
                    failed_facebook_profile_count += 1
                else:
                    pending_facebook_profile_count += 1
            total_facebook_profile_count = completed_facebook_profile_count + failed_facebook_profile_count + pending_facebook_profile_count
            total_facebook_profile_count = completed_facebook_profile_count + failed_facebook_profile_count + pending_facebook_profile_count
            return JsonResponse({'pending_vehicle_listings_count': pending_vehicle_listings_count, 'failed_vehicle_listings_count': failed_vehicle_listings_count, 'completed_vehicle_listings_count': completed_vehicle_listings_count,'total_vehicle_listings': total_vehicle_listings_count,'pending_gumtree_profile_count': pending_gumtree_profile_count, 'failed_gumtree_profile_count': failed_gumtree_profile_count, 'completed_gumtree_profile_count': completed_gumtree_profile_count,'total_gumtree_profile_count': total_gumtree_profile_count,'pending_facebook_profile_count': pending_facebook_profile_count, 'failed_facebook_profile_count': failed_facebook_profile_count, 'completed_facebook_profile_count': completed_facebook_profile_count,'total_facebook_profile_count': total_facebook_profile_count}, status=200)
        else:
            return JsonResponse({'pending_vehicle_listings_count': pending_vehicle_listings_count, 'failed_vehicle_listings_count': failed_vehicle_listings_count, 'completed_vehicle_listings_count': completed_vehicle_listings_count,'total_vehicle_listings': total_vehicle_listings_count,'pending_gumtree_profile_count': pending_gumtree_profile_count, 'failed_gumtree_profile_count': failed_gumtree_profile_count, 'completed_gumtree_profile_count': completed_gumtree_profile_count,'total_gumtree_profile_count': total_gumtree_profile_count,'pending_facebook_profile_count': pending_facebook_profile_count, 'failed_facebook_profile_count': failed_facebook_profile_count, 'completed_facebook_profile_count': completed_facebook_profile_count,'total_facebook_profile_count': total_facebook_profile_count}, status=200)
        
    return JsonResponse({'error': 'Invalid request method'}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_facebook_session_status(request):
    """Get Facebook session status"""
    if request.method == 'GET':
        user = request.user
        credentials = FacebookUserCredentials.objects.filter(user=user).first()
        if credentials: 
            return JsonResponse({'facebook_session_status': credentials.status}, status=200)
        else:
            return JsonResponse({'facebook_session_status': False}, status=200)
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_gumtree_profile_vehicle_listings(request):
    """Get vehicle listings from specific Gumtree profile URL"""
    user = request.user
    gumtree_profile_url = request.GET.get('url')
    
    if not gumtree_profile_url:
        return JsonResponse({'error': 'url parameter is required'}, status=400)
    
    # Verify the profile belongs to the user
    gumtree_profile = GumtreeProfileListing.objects.filter(
        user=user, 
        url=gumtree_profile_url
    ).first()
    
    if not gumtree_profile:
        return JsonResponse({'error': 'Gumtree profile not found or does not belong to user'}, status=404)
    
    vehicle_listings = VehicleListing.objects.filter(
        user=user, 
        status="pending",
        gumtree_profile=gumtree_profile
    ).select_related('gumtree_profile').order_by('-updated_at')
    
    serializer = VehicleListingSerializer(vehicle_listings, many=True)
    return JsonResponse({
        'count': vehicle_listings.count(),
        'gumtree_profile_url': gumtree_profile_url,
        'results': serializer.data
    }, status=200)



def image_verification(relisting,vehicle_listing):
    """Verify image upload and update the status of the vehicle listing"""
    response=image_upload_verification(relisting,vehicle_listing)
    if relisting:
        if response[0] == 1:
            relisting.listing.has_images=True
            relisting.listing.save()
        elif response[0] == 0:
            relisting.status="failed"
            relisting.save()
        elif response[0] == 7:
            if relisting.listing.retry_count < 5:
                relisting.listing.retry_count += 1
                relisting.listing.save()
            else:
                relisting.listing.status = "sold" 
                relisting.listing.save()
                relisting.status="completed"
                relisting.last_relisting_status=True
                relisting.save()
        else:
            pass
    elif vehicle_listing and vehicle_listing.is_relist == True:
        failed_relisting = RelistingFacebooklisting.objects.filter(listing=vehicle_listing, user=vehicle_listing.user, status__in=["completed", "failed"], last_relisting_status=False).first()
        if failed_relisting:
            if response[0] == 1:
                failed_relisting.listing.has_images=True
                failed_relisting.listing.save()
            elif response[0] == 0:
                failed_relisting.status="failed"
                failed_relisting.save()
            elif response[0] == 7:
                if failed_relisting.listing.retry_count < MAX_RETRIES_ATTEMPTS:
                    failed_relisting.listing.retry_count += 1
                    failed_relisting.listing.save()
                else:
                    failed_relisting.listing.status = "sold" 
                    failed_relisting.listing.save()
                    failed_relisting.status="completed"
                    failed_relisting.last_relisting_status=True
                    failed_relisting.save()
    else:
        if response[0] == 1:
            vehicle_listing.has_images=True
            vehicle_listing.save()
        elif response[0] == 0:
            vehicle_listing.status="failed"
            vehicle_listing.save()
        elif response[0] == 7:
            if vehicle_listing.retry_count < 3:
                vehicle_listing.retry_count += 1
                vehicle_listing.save()
            else:
                vehicle_listing.status = "sold"
                vehicle_listing.save()
        else:
            pass


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_vehicle_listing_listed_on(request):
    """
    Update the listed_on date for a specific vehicle listing
    
    Request Body:
    {
        "id": 123,
        "listed_on": "2024-01-15T10:30:00Z"
    }
    
    Returns:
    {
        "success": true,
        "message": "Vehicle listing listed_on date updated successfully",
        "data": {
            "id": 123,
            "listed_on": "2024-01-15T10:30:00Z"
        }
    }
    """
    try:
        # Parse request data
        data = json.loads(request.body)
        
        # Validate required fields
        if 'id' not in data:
            return JsonResponse({
                'success': False,
                'error': 'Vehicle listing ID is required'
            }, status=400)
            
        if 'listed_on' not in data:
            return JsonResponse({
                'success': False,
                'error': 'listed_on date is required'
            }, status=400)
        
        vehicle_listing_id = data['id']
        listed_on_date = data['listed_on']
        
        # Validate that the ID is a positive integer
        try:
            vehicle_listing_id = int(vehicle_listing_id)
            if vehicle_listing_id <= 0:
                raise ValueError("ID must be positive")
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Invalid vehicle listing ID format'
            }, status=400)
        
        # Parse and validate the date
        try:
            # Parse ISO format datetime
            if isinstance(listed_on_date, str):
                listed_on_datetime = datetime.fromisoformat(listed_on_date.replace('Z', '+00:00'))
            else:
                return JsonResponse({
                    'success': False,
                    'error': 'listed_on must be a valid ISO datetime string'
                }, status=400)
        except ValueError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid datetime format. Use ISO format (e.g., 2024-01-15T10:30:00Z)'
            }, status=400)
        
        # Get the vehicle listing and check ownership
        try:
            vehicle_listing = get_object_or_404(VehicleListing, id=vehicle_listing_id, user=request.user)
        except VehicleListing.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Vehicle listing not found or you do not have permission to update it'
            }, status=404)
        
        # Update the listed_on field
        vehicle_listing.listed_on = listed_on_datetime
        vehicle_listing.status = "completed"
        vehicle_listing.save()
        
        # Return success response
        return JsonResponse({
            'success': True,
            'message': 'Vehicle listing listed_on date updated successfully',
            'data': {
                'id': vehicle_listing.id,
                'listed_on': vehicle_listing.listed_on.isoformat() if vehicle_listing.listed_on else None,
                'updated_at': vehicle_listing.updated_at.isoformat()
            }
        }, status=200)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON format in request body'
        }, status=400)
        
    except Exception as e:
        logger.error(f"Error updating vehicle listing listed_on date: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'An unexpected error occurred while updating the vehicle listing'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_old_vehicle_listings(request):
    """Get vehicle listings that are older than specified days based on listed_on date"""
    user = request.user
    
    # Get days parameter with validation
    try:
    
        # Calculate the cutoff datetime (10 minutes ago)
        cutoff_date = timezone.now().date() - timedelta(days=7)
        # Get user's vehicle listings older than the cutoff datetime
        vehicle_listings = VehicleListing.objects.filter(
        status="completed", listed_on__date__lte=cutoff_date, is_relist=False,user=user
    ).order_by("listed_on")
        
        # Serialize the listings using the existing serializer
        serializer = VehicleListingSerializer(vehicle_listings, many=True)
        
        return JsonResponse({
            'count': vehicle_listings.count(),
            'results': serializer.data
        }, status=200)
    except Exception as e:
        logger.error(f"Error retrieving old vehicle listings: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'An unexpected error occurred while retrieving old vehicle listings: {str(e)}'
        }, status=500)