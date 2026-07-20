from .gumtree_scraper import get_gumtree_listings,format_car_description,is_gumtree_listing_active
from .custom_domain_scraper import get_custom_domain_listings
from .custom_domain_adapters import resolve_for_url, any_needs_image_proxy
from .url_importer import ImportFromUrl
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework import filters
from .serializers import VehicleListingSerializer, ListingUrlSerializer, FacebookUserCredentialsSerializer,FacebookProfileListingSerializer,GumtreeProfileListingSerializer,CustomDomainProfileListingSerializer,CustomDomainVehicleListingSerializer,ProductListSerializer,ProductDetailSerializer,DealerListSerializer
from accounts.models import User
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookListing,GumtreeProfileListing,FacebookProfileListing,RelistingFacebooklisting,CustomDomainProfileListing,FacebookListingSnapshot,UnpublishedListingSnapshot,ExtensionSyncStatus
import json
# from .facebook_listing import create_marketplace_listing, perform_search_and_delete, get_facebook_profile_listings, extract_facebook_listing_details, image_upload_verification
from .utils import send_status_reminder_email, mark_listing_sold
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
import requests as _http_requests
import threading
from rest_framework.decorators import api_view, permission_classes
import time
import random   
from datetime import datetime, timedelta
from django.db import transaction
from django.db.models import F, Q, Count, IntegerField, Max, Case, When, Value
from django.db.models.functions import Cast
from queue import Queue
from django.conf import settings
from django.utils import timezone
import logging
from django.shortcuts import get_object_or_404

logger = logging.getLogger('relister_views')

# dictionary which hold queues for each user
# vehicle_listing_user_queues = {}
# user_queues = {}
# gumtree_profile_user_queues = {}
# facebook_profile_user_queues = {}
# # worker function to process the vehicle listings
# def worker(user_id):
#     while True:
#         vehicle_listing = user_queues[user_id].get()
#         if vehicle_listing is None:
#             break
#         create_facebook_listing(vehicle_listing)
#         time.sleep(random.randint(20, 30))
#         image_verification(None,vehicle_listing)
#         user_queues[user_id].task_done()

# def delete_vehicle_listing_worker(user_id):
#     while True:
#         search_query,price,listed_on,session_cookie = vehicle_listing_user_queues[user_id].get()
#         if search_query is None or price is None or listed_on is None or session_cookie is None:
#             break
#         for retry in range(3):
#             response = perform_search_and_delete(search_query,price,listed_on,session_cookie)
#             if response[0] == 1:
#                 break
#             elif response[0] == 6:
#                 break
#             else:
#                 time.sleep(300,360)
#         vehicle_listing_user_queues[user_id].task_done()


# def delete_facebook_profile_listing_worker(user_id):
#     while True:
#         year_make_model_list,session_cookie = facebook_profile_user_queues[user_id].get()
#         if year_make_model_list is None or session_cookie is None:
#             break
#         delete_multiple_vehicle_listings(year_make_model_list,session_cookie)
#         facebook_profile_user_queues[user_id].task_done()

# def delete_gumtree_profile_listing_worker(user_id):
#     while True:
#         year_make_model_list,session_cookie = gumtree_profile_user_queues[user_id].get()
#         if year_make_model_list is None or session_cookie is None:
#             break
#         delete_multiple_vehicle_listings(year_make_model_list,session_cookie)
#         gumtree_profile_user_queues[user_id].task_done()

# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# def import_url_from_gumtree(request):
#     """Import URL from Gumtree"""
#     if request.method == 'POST':
#         data = json.loads(request.body)
#         url = data[0].get('url')
#         user = request.user
        
#         # Initialize a queue for the user if it doesn't exist
#         if user.id not in user_queues:
#             user_queues[user.id] = Queue()
#             threading.Thread(target=worker, args=(user.id,), daemon=True).start()
        
#         import_url = ImportFromUrl(url)
#         is_valid, error_message = import_url.validate()

#         if not is_valid:
#             return JsonResponse({'error': error_message}, status=200)
#         list_id = extract_seller_id(url)
#         if not list_id or not list_id.isdigit():
#             return JsonResponse({'error': 'Invalid seller ID'}, status=200)
#         if ListingUrl.objects.filter(url=url,user=user,listing_id=list_id).exists():
#             return JsonResponse({'error': 'URL already exists'}, status=200)
#         if VehicleListing.objects.filter(user=user,list_id=list_id).exists():              
#             return JsonResponse({'error': 'Listing already exists '}, status=200)
#         if import_url.print_url_type() == "Facebook Profile" or import_url.print_url_type() == "Gumtree Profile":
#             return  JsonResponse({'error': 'This is Facebook Profile Url, Now, Only Process the Gumtree and Facebook single Url'}, status=200)
#         credentials = FacebookUserCredentials.objects.filter(user=user).first()
#         if not credentials or credentials.session_cookie == {} or not credentials.status:
#             if credentials:
#                 credentials.status = False
#                 credentials.save()
#                 send_status_reminder_email(credentials)
#             return JsonResponse({'error': 'No facebook credentials found for the user , Please provide the facebook credentials'}, status=200)
#         if import_url.print_url_type() == "Facebook":
#             # Extract facebook listing data from URL
#             current_listing = {}
#             current_listing['url'] = url
#             current_listing['mileage'] = None
#             import_url_instance = ListingUrl.objects.create(url=url, user=user , status='pending')
#             response = extract_facebook_listing_details(current_listing,credentials.session_cookie)
#             if response and response.get("year") and response.get("make") and response.get("model") and response["images"]:
#                 # Get description and mileage
#                 description = response.get("description")
#                 mileage = response["driven"] if response["driven"] else current_listing["mileage"]
                
#                 # Add mileage to description if not already present
#                 if mileage:
#                     mileage=int(''.join(filter(str.isdigit, mileage)))
#                     description_lines = description.splitlines()
#                     mileage_text = "Mileage: " + str(mileage) + "km"
                    
#                     # Check if mileage is already in description (case-insensitive)
#                     if mileage_text.lower() not in description.lower():
#                         # Insert mileage as the first line
#                         description_lines.insert(0, mileage_text)
                        
#                         # Update the description
#                         description = "\n".join(description_lines)
                
#                 enhanced_description=format_car_description(description)
#                 vehicle_listing = VehicleListing.objects.create(
#                     user=user,
#                     list_id=list_id,
#                     gumtree_url=import_url_instance,
#                     year=response["year"],
#                     body_type="Other",
#                     fuel_type=response["fuel_type"],
#                     color=None,
#                     variant="Other",
#                     make=response["make"],
#                     mileage=mileage,
#                     model=response["model"],
#                     price=response.get("price"),
#                     transmission=response["transmission"],
#                     description=enhanced_description,
#                     exterior_colour=response["exterior_colour"],
#                     interior_colour=response["interior_colour"],
#                     condition=response["condition"],
#                     images=response["images"],
#                     url=current_listing["url"],
#                     location=response.get("location"),
#                     status="pending",
#                     )
#                 import_url_instance.status = "completed"
#                 import_url_instance.save()
#                 credentials.status = True
#                 credentials.retry_count = 0
#                 credentials.save()
#                 user_queues[user.id].put(vehicle_listing)
#                 user_data = {
#                     'url': url,
#                     'message': "Extracted data successfully and listing created in facebook is in progress"
#                 }
#                 return JsonResponse(user_data, status=200)
#             elif not response.get("year") or not response.get("make") or not response.get("model") or not response["images"]:
#                 import_url_instance.delete()
#                 return JsonResponse({'error': 'Failed to extract required Facebook listing details. Please verify and save your Facebook login session, check the URL, and try again.'}, status=200)
#             else:
#                 if credentials.retry_count < settings.MAX_RETRIES_ATTEMPTS:
#                     credentials.retry_count += 1
#                     credentials.save()
#                     import_url_instance.delete()
#                     return JsonResponse({'error': 'Failed to extract Facebook listing details. Please verify and save your Facebook login session, check the URL, and try again.'}, status=200)
#                 else:
#                     credentials.status = False
#                     credentials.save()
#                     import_url_instance.delete()
#                     return JsonResponse({'error': 'Failed to extract Facebook listing details. Please verify and save your Facebook login session, check the URL, and try again.'}, status=200)
#         # Extract Gumtree data from URL
#         else:
#             import_url_instance = ListingUrl.objects.create(url=url, user=user , status='pending')
#             vehicle_listing = get_listings(url,user,import_url_instance)
#             if vehicle_listing:
#                 vls = VehicleListingSerializer(vehicle_listing)
#                 print(f"vehicle_listing: {vehicle_listing}")
#                 user_queues[user.id].put(vehicle_listing)
#                 # Prepare user related listing data
#                 user_data = {
#                     'url': url,
#                     'message': "Extracted data successfully and listing created in facebook is in progress"
#                 }
#                 return JsonResponse(user_data, status=200)
#             else:
#                 import_url_instance.delete()
#                 return JsonResponse({'error': 'Failed to extract data from URL, Check the URL, Zenrows API key and try again'}, status=200)

#     return JsonResponse({'error': 'Invalid request method'}, status=405)


# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# def all_vehicle_listing(request):
#     """Get all vehicle listings"""
#     if request.method == 'POST':
#         user = request.user
#         all_vehicle_listing = VehicleListing.objects.filter(user=user)
#         if all_vehicle_listing.exists():
#             return JsonResponse(list(all_vehicle_listing.values()), safe=False, status=200)
#         else:
#             return JsonResponse({'error': 'No vehicle listings found'}, status=200)
#     return JsonResponse({'error': 'Invalid request method'}, status=405)

# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# def all_urls(request):
#     """Get all URLs"""
#     if request.method == 'POST':
#         user = request.user
#         all_urls = ListingUrl.objects.filter(user=user)
#         return JsonResponse(list(all_urls.values()), safe=False, status=200)
#     return JsonResponse({'error': 'Invalid request method'}, status=405)
# class ListingUrlViewSet(ModelViewSet):
#     """Get all URLs"""
#     queryset = ListingUrl.objects.all()
#     serializer_class = ListingUrlSerializer
#     permission_classes = [IsAuthenticated]
#     filter_backends = [filters.SearchFilter]
#     search_fields = ['url']
#     filterset_fields = ['url']
#     ordering_fields = ['url']
#     ordering = ['url']

#     def get_queryset(self):
#         if getattr(self, 'swagger_fake_view', False):
#             return ListingUrl.objects.none()
#         user = self.request.user
#         if user.is_superuser:
#             return ListingUrl.objects.all()
#         else:
#             return ListingUrl.objects.filter(user=user)
        
#     def destroy(self, request, *args, **kwargs):
#         """Delete listing URL"""
#         instance = self.get_object()
#         import_url = ListingUrl.objects.filter(id=instance.id).first()  
#         vehicle_listing = VehicleListing.objects.filter(gumtree_url=import_url).first()
#         if vehicle_listing:
#             search_query = vehicle_listing.year + " " + vehicle_listing.make + " " + vehicle_listing.model
#             credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
#             if vehicle_listing.status in ["pending", "failed", "sold"]:
#                 import_url.delete()
#                 vehicle_listing.delete()
#                 return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
#             else:
#                 if credentials and credentials.session_cookie and credentials.status:
#                     if vehicle_listing.listed_on and not vehicle_listing.is_relist:
#                         listed_on = timezone.localtime(vehicle_listing.listed_on)
#                     else:
#                         relisting=RelistingFacebooklisting.objects.filter(listing=vehicle_listing,status="completed",last_relisting_status=False).first()
#                         if relisting:
#                             listed_on = timezone.localtime(relisting.relisting_date)
#                         else:
#                             listed_on = timezone.localtime(timezone.now())
#                     price = vehicle_listing.price
#                     user_id = vehicle_listing.user.id
#                     if user_id not in vehicle_listing_user_queues:
#                         vehicle_listing_user_queues[user_id] = Queue()
#                         threading.Thread(target=delete_vehicle_listing_worker, args=(user_id,), daemon=True).start()
#                     vehicle_listing_user_queues[user_id].put((search_query, price, listed_on, credentials.session_cookie))
#                     import_url.delete()
#                     vehicle_listing.delete()
#                     return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
#                 else:
#                     if credentials:
#                         credentials.status = False
#                         credentials.save()
#                         send_status_reminder_email(credentials)
#                     import_url.delete()
#                     vehicle_listing.delete()
#                     return JsonResponse({'error': 'No facebook credentials found for the user'}, status=200)
#         else:
#             import_url.delete()
#             return JsonResponse({'message': 'Listing URL deleted successfully but vehicle listing not found'}, status=200)

# class VehicleListingViewSet(ModelViewSet):
#     """Get all vehicle listings with relisting dates"""
#     queryset = VehicleListing.objects.all().order_by('-updated_at')
#     serializer_class = VehicleListingSerializer  # Use the updated serializer
#     permission_classes = [IsAuthenticated]
#     filter_backends = [filters.SearchFilter]
#     search_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
#     filterset_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
#     ordering_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
#     ordering = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    
#     def get_queryset(self):
#         """Get all vehicle listings with relisting dates"""
#         if getattr(self, 'swagger_fake_view', False):
#             return VehicleListing.objects.none()
#         user = self.request.user
#         if user.is_superuser:
#             return VehicleListing.objects.all().order_by('-updated_at')
#         else:
#             return VehicleListing.objects.filter(user=user).order_by('-updated_at')

#     def destroy(self, request, *args, **kwargs):
#         """Delete vehicle listing"""
#         instance = self.get_object()
#         print(f"instance: {instance}")
#         # Proceed with deletion
#         vehicle_listing=VehicleListing.objects.filter(id=instance.id).first()
#         search_query = vehicle_listing.year + " " + vehicle_listing.make + " " + vehicle_listing.model
#         credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
#         if vehicle_listing.status== "pending" or vehicle_listing.status== "failed" or vehicle_listing.status== "sold":
#             vehicle_listing.delete()
#             return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
#         else:
#             if credentials and credentials.session_cookie != {} and credentials.status:
#                 if vehicle_listing.listed_on and not vehicle_listing.is_relist:
#                     listed_on = timezone.localtime(vehicle_listing.listed_on)
#                 else:
#                     relisting=RelistingFacebooklisting.objects.filter(listing=vehicle_listing,status="completed",last_relisting_status=False).first()
#                     if relisting:
#                         listed_on = timezone.localtime(relisting.relisting_date)
#                     else:
#                         listed_on = timezone.localtime(vehicle_listing.listed_on)
#                 price = vehicle_listing.price
#                 user_id = vehicle_listing.user.id
#                 if user_id not in vehicle_listing_user_queues:
#                     vehicle_listing_user_queues[user_id] = Queue()
#                     threading.Thread(target=delete_vehicle_listing_worker, args=(user_id,), daemon=True).start()
#                 vehicle_listing_user_queues[user_id].put((search_query,price,listed_on,credentials.session_cookie))
#                 vehicle_listing.delete()
#                 return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
#             else:
#                 if credentials:
#                     credentials.status = False
#                     credentials.save()
#                     send_status_reminder_email(credentials)
#                 vehicle_listing.delete()
#                 return JsonResponse({'error': 'No facebook credentials found for the user'}, status=200)

# class FacebookUserCredentialsViewSet(ModelViewSet):
#     """Get all Facebook user credentials"""
#     queryset = FacebookUserCredentials.objects.all()
#     serializer_class = FacebookUserCredentialsSerializer
#     permission_classes = [IsAuthenticated]
#     filter_backends = [filters.SearchFilter]
#     search_fields = ['email']
#     filterset_fields = ['email']
#     ordering_fields = ['email']
#     ordering = ['email']

#     def get_queryset(self):
#         """Get all Facebook user credentials"""
#         if getattr(self, 'swagger_fake_view', False):
#             return FacebookUserCredentials.objects.none()
#         user = self.request.user
#         if user.is_superuser:
#             return FacebookUserCredentials.objects.all()
#         else:
#             return FacebookUserCredentials.objects.filter(user=user)

#     def create(self, request, *args, **kwargs):
#         """Create Facebook user credentials"""
#         data = json.loads(request.body)
#         user_id = data.get('user')
#         cookies = data.get('cookies')
#         origins = data.get('origins')
#         user = User.objects.filter(id=user_id).first()
#         if not user:
#             return JsonResponse({'error': 'User not found'}, status=200)
#         enhanced_cookies = []
#         if cookies:
#             excluded_cookies = {"dbln", "ps_l", "ps_n", "ar_debug"}
#             lax_cookies = {"wd", "presence"}

#             for cookie in cookies:
#                 if cookie["name"] in excluded_cookies:
#                     continue  # Skip excluded cookies
#                 same_site_value = "Lax" if cookie["name"] in lax_cookies else "None"
#                 enhanced_cookie = {
#                     "name": cookie["name"],
#                     "path": cookie["path"],
#                     "value": cookie["value"],
#                     "domain": cookie["domain"],
#                     "secure": cookie["secure"],
#                     "httpOnly": cookie["httpOnly"],
#                     "sameSite": same_site_value
#                 }

#                 if "expires" in cookie:
#                     enhanced_cookie["expires"] = cookie["expires"]

#                 enhanced_cookies.append(enhanced_cookie)
#         session_cookie = {
#             "cookies": enhanced_cookies,
#             "origins": origins
#         }
#         already_exist_session_cookie = FacebookUserCredentials.objects.filter(user=user).first()
#         if already_exist_session_cookie:
#             already_exist_session_cookie.session_cookie = session_cookie
#             already_exist_session_cookie.status = True
#             already_exist_session_cookie.status_reminder = False
#             already_exist_session_cookie.retry_count = 0
#             already_exist_session_cookie.save()
#             return JsonResponse({'message': 'Facebook user credentials updated successfully'}, status=200)
#         else:
#             FacebookUserCredentials.objects.create(user=user, session_cookie=session_cookie,status=True,retry_count=0,status_reminder=False)
#             return JsonResponse({'message': 'Facebook user credentials created successfully'}, status=200)

# def create_facebook_listing(vehicle_listing):
#     """Create Facebook listing"""
#     try:
#         user = vehicle_listing.user
#         credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
#         if credentials and credentials.session_cookie != {} and credentials.status:
#             already_listed = FacebookListing.objects.filter(user=vehicle_listing.user, listing=vehicle_listing,).first()
#             if not already_listed:
#                 time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
#                 current_user=User.objects.filter(id=user.id).first()
#                 last_day_time = timezone.now() - timedelta(hours=24)
#                 if current_user.last_facebook_listing_time and current_user.last_facebook_listing_time > last_day_time:
#                     current_user.daily_listing_count = 0
#                     current_user.save()
#                 if current_user.daily_listing_count >= settings.MAX_DAILY_LISTINGS_COUNT:
#                     vehicle_listing.status="failed"
#                     vehicle_listing.save()
#                     return False, "Daily listing count limit reached"
#                 logger.info(f"Creating listing for the user {user.email} and listing title: {vehicle_listing.year} {vehicle_listing.make} {vehicle_listing.model}")
#                 listing_created, message = create_marketplace_listing(vehicle_listing, credentials.session_cookie)
#                 if listing_created:
#                     FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="success")
#                     credentials.status = True
#                     credentials.retry_count = 0
#                     credentials.save()
#                     vehicle_listing.status="completed"
#                     vehicle_listing.listed_on=timezone.now()
#                     vehicle_listing.updated_at=timezone.now()
#                     vehicle_listing.save()
#                     current_user.last_facebook_listing_time = timezone.now()
#                     current_user.daily_listing_count += 1
#                     current_user.save()
#                     return True, "Listing created successfully"
#                 else:
#                     if credentials.retry_count < settings.MAX_RETRIES_ATTEMPTS:
#                         credentials.retry_count += 1
#                         credentials.save()
#                         vehicle_listing.status="failed"
#                         vehicle_listing.save()
#                         return False, "Failed to create listing"
#                     else:
#                         credentials.status = False
#                         credentials.save()
#                         vehicle_listing.status="failed"
#                         vehicle_listing.save()
#                         return False, "Failed to create listing"
#             else:
#                 return True, "Listing already exists"
#         else:
#             if credentials:
#                 credentials.status = False
#                 credentials.save()
#                 send_status_reminder_email(credentials)
#             vehicle_listing.status="failed"
#             vehicle_listing.save()
#             return False, "No facebook credentials found for the user"
             
#     except Exception as e:
#         vehicle_listing.status="failed"
#         vehicle_listing.save()
#         return False, "Failed to create listing"
    

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def get_gumtree_profile_listings(request):
    """Get Gumtree profile listings"""
    try:
        data = json.loads(request.body)
        profile_url = data.get('gumtree_profile_url')
        user = request.user
        import_url = ImportFromUrl(profile_url)
        is_valid, error_message = import_url.validate()
        if not is_valid:
            return JsonResponse({'error': error_message}, status=400)
        if import_url.print_url_type() == "Facebook" or import_url.print_url_type() == "Facebook Profile":
            return JsonResponse({'error': 'Please provide the Gumtree Profile Url'}, status=400)
        seller_id = extract_seller_id(profile_url)
        if not seller_id or not seller_id.isdigit():
            return JsonResponse({'error': 'Invalid seller ID'}, status=400)
        if GumtreeProfileListing.objects.filter(url=profile_url,user=user,profile_id=seller_id).exists():
            return JsonResponse({'error': 'This URL is already processed'}, status=409)
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
def get_custom_domain_profile_listings(request):
    """Submit a custom-domain URL and start scraping its stock."""
    try:
        data = json.loads(request.body)
        profile_url = data.get('custom_domain_url')
        user = request.user
        if not profile_url:
            return JsonResponse({'error': 'custom_domain_url is required'}, status=400)
        adapter = resolve_for_url(profile_url)
        if adapter is None:
            return JsonResponse({
                'error': 'Invalid custom_domain_url. Must be a full http(s) URL with a host.'
            }, status=400)
        if CustomDomainProfileListing.objects.filter(url=profile_url, user=user, profile_id=adapter.HOST).exists():
            return JsonResponse({'error': 'This URL is already processed'}, status=409)
        success, message = get_custom_domain_listings(profile_url, user)
        if success:
            return JsonResponse({'message': message}, status=200)
        else:
            return JsonResponse({'error': message}, status=200)

    except Exception as e:
        return JsonResponse({'message': str(e)}, status=500)

# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# def facebook_profile_listings(request):
#     """Get Facebook profile listings"""
#     try:
#         data = json.loads(request.body)
#         profile_url = data.get('profile_url')
#         user = request.user
#         if not profile_url:
#             return JsonResponse({'error': 'Profile URL is required'}, status=400)
#         import_url = ImportFromUrl(profile_url)
#         is_valid, error_message = import_url.validate()

#         if not is_valid:
#             return JsonResponse({'error': error_message}, status=200)
#         if import_url.print_url_type() == "Gumtree" or import_url.print_url_type() == "Gumtree Profile" or import_url.print_url_type() == "Facebook":
#             return  JsonResponse({'error': 'Please provide the Facebook Profile Url'}, status=200)
#         seller_id = extract_seller_id(profile_url)
#         if not seller_id or not seller_id.isdigit():
#             return JsonResponse({'error': 'Invalid seller ID'}, status=200)
#         if FacebookProfileListing.objects.filter(url=profile_url,user=user,profile_id=seller_id).exists():
#                 return JsonResponse({'error': 'This URL is already processed'}, status=200)
#         # Get user's Facebook credentials
#         credentials = FacebookUserCredentials.objects.filter(user=user).first()
#         if not credentials or credentials.session_cookie == {} or not credentials.status:
#             if credentials:
#                 credentials.status = False
#                 credentials.save()
#                 send_status_reminder_email(credentials)
#             return JsonResponse({'error': 'Facebook credentials not found'}, status=404)
#         logger.info(f"start getting listings for the user {user.email} and profile id: {seller_id}")
#         success, listings = get_facebook_profile_listings(profile_url, credentials.session_cookie)

#         if success:
#             credentials.status = True
#             credentials.retry_count=0
#             credentials.save()
#             logger.info(f"Creating facebook profile listing for the user {user.email} and profile id: {seller_id}")
#             logger.info(f"Total listings: {len(listings)}")
#             facebook_profile_listing_instance = FacebookProfileListing.objects.create(url=profile_url,user=user,status="pending",profile_id=seller_id,total_listings=len(listings))
#             # Create a new thread to process the listings
#             thread = threading.Thread(target=facebook_profile_listings_thread, args=(listings, credentials,user,seller_id,facebook_profile_listing_instance))
#             thread.start()
#             return JsonResponse({'message': 'Profile Listings are being processed'}, status=200)
#         else:
#             if credentials.retry_count < settings.MAX_RETRIES_ATTEMPTS:
#                 credentials.retry_count += 1
#                 credentials.save()
#                 return JsonResponse({'error': 'Failed to get listings'}, status=200)
#             else:
#                 credentials.status = False
#                 credentials.save()
#                 return JsonResponse({'error': 'Failed to get listings'}, status=200)

#     except json.JSONDecodeError:
#         return JsonResponse({'error': 'Invalid JSON data'}, status=400)
#     except Exception as e:
#         return JsonResponse({'error': str(e)}, status=500)
    

# def facebook_profile_listings_thread(listings, credentials,user,seller_id,facebook_profile_listing_instance):
#     """Get listings using the function"""
#     logger.info(f"Getting listings for the user {user.email} and profile id: {seller_id}")
#     count=0
#     incoming_list_ids = set()
#     logger.info(f"Total listings to process: {len(listings)}")
#     for current_listing_key in listings:
#         current_listing=listings[current_listing_key]
#         logger.info(f"Processing listing: {current_listing}")
#         already_listed = VehicleListing.objects.filter(user=user, list_id=current_listing["id"]).first()
#         incoming_list_ids.add(str(current_listing["id"]))
#         if already_listed and already_listed.status == "sold":
#             count+=1
#             logger.info(f"Vehicle listing already exists and marked as sold for the user {user.email} and listing title: {already_listed.year} {already_listed.make} {already_listed.model} and profile id: {seller_id}")
#             # If the listing is already listed, delete it from the FacebookProfileListing
#             already_listed.delete()
#             continue
#         elif already_listed:
#             count+=1
#             logger.info(f"Vehicle listing already exists for the user {user.email} and listing title: {already_listed.year} {already_listed.make} {already_listed.model} and profile id: {seller_id}")
#             continue
#         else:
#             time.sleep(random.uniform(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
#             # Get listings using the function
#             vehicleListing=extract_facebook_listing_details(current_listing, credentials.session_cookie)
#             if vehicleListing:
#                 logger.info(f"Vehicle listing found for the user {user.email} and listing title: {vehicleListing.get('year')} {vehicleListing.get('make')} {vehicleListing.get('model')} and profile id: {seller_id}")
#                 count+=1
                
#                 # Get description and mileage
#                 description = vehicleListing.get("description")
#                 mileage = vehicleListing["driven"] if vehicleListing["driven"] else current_listing["mileage"]
                
#                 # Add mileage to description if not already present
#                 if mileage:
#                     mileage=int(''.join(filter(str.isdigit, mileage)))
#                     description_lines = description.splitlines()
#                     mileage_text = "Mileage: " + str(mileage) + "km"
                    
#                     # Check if mileage is already in description (case-insensitive)
#                     if mileage_text.lower() not in description.lower():
#                         # Insert mileage as the first line
#                         description_lines.insert(0, mileage_text)
                        
#                         # Update the description
#                         description = "\n".join(description_lines)
                
#                 enhanced_description=format_car_description(description)
#                 VehicleListing.objects.create(
#                     user=user,
#                     facebook_profile=facebook_profile_listing_instance,
#                     list_id=current_listing["id"],
#                     year=vehicleListing["year"],
#                     body_type="Other",
#                     fuel_type=vehicleListing["fuel_type"],
#                     color=None,
#                     variant="Other",
#                     make=vehicleListing["make"],
#                     mileage=mileage,
#                     model=vehicleListing["model"],
#                     price=vehicleListing.get("price"),
#                     transmission=vehicleListing["transmission"],
#                     condition=vehicleListing["condition"],
#                     description=enhanced_description,
#                     images=vehicleListing["images"],
#                     url=current_listing["url"],
#                     location=vehicleListing.get("location"),
#                     status="pending",
#                     exterior_colour=vehicleListing["exterior_colour"],
#                     interior_colour=vehicleListing["interior_colour"],
#                     seller_profile_id=seller_id
#                 )
#             else:
#                 logger.error(f"Failed to extract Facebook listing details for the user {user.email} and profile id: {seller_id}")
#                 if credentials.retry_count < settings.MAX_RETRIES_ATTEMPTS:
#                     credentials.retry_count += 1
#                     credentials.save()
#                     continue
#                 else:
#                     credentials.status = False
#                     credentials.save()
#                     logger.error(f"Failed to extract Facebook listing details for the user {user.email} and profile id: {seller_id}, Max retries reached")
#                     break
#     facebook_profile_listing_instance.processed_listings = count
#     facebook_profile_listing_instance.status = "completed"
#     facebook_profile_listing_instance.save()
#     #Mark missing listings as SOLD
#     existing_listings = VehicleListing.objects.filter(
#         user=user, seller_profile_id=seller_id
#     ).exclude(list_id__in=incoming_list_ids)
#     if existing_listings:
#         for listing in existing_listings:
#             if listing.status != "completed":
#                 logging.info(f"Marking listing ID {listing.list_id} as sold (deleting)")
#                 listing.delete()
#                 continue
#             elif listing.status == "completed":
#                 search_query = f"{listing.year} {listing.make} {listing.model}"
#                 price = listing.price
#                 listed_on = timezone.localtime(listing.listed_on)
#                 credentials = FacebookUserCredentials.objects.filter(user=listing.user).first()
#                 if credentials and listing.is_relist:
#                     relisting = RelistingFacebooklisting.objects.filter(listing=listing, user=listing.user, status__in=["completed", "failed"], last_relisting_status=False).first()
#                     if relisting and relisting.status == "failed":
#                         logging.info(f"Relisting status is failed for {search_query}, deleting listing")
#                         listing.delete()
#                         continue
#                     elif relisting and relisting.status == "completed":
#                         listed_on = timezone.localtime(relisting.relisting_date)
#                         time.sleep(random.uniform(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
#                         response = perform_search_and_delete(search_query, price, listed_on, credentials.session_cookie)
#                         if response[0] == 1:
#                             logging.info(f"Deleted relisted Facebook listing for {search_query}")
#                             listing.delete()
#                         elif response[0] == 6:
#                             logger.info("Listing not found for relisting and listing title is {listing.year} {listing.make} {listing.model} for the user {user.email}")
#                             logger.info(f"response[1]: {response[1]}")
#                             listing.delete()
#                         elif response[0] == 4:
#                             logger.info(f"Got issue inside the automation for deleting the relisting {listing.year} {listing.make} {listing.model} and for user {user.email} ")
#                             logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
#                             logger.info(f"response[1]: {response[1]}")
#                             logger.info(f"number of retries: {listing.retry_count}")
#                             if listing.retry_count <= MAX_RETRIES_ATTEMPTS:
#                                 listing.retry_count += 1
#                                 listing.save()
#                                 logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} and number of retries: {listing.retry_count}")
#                             else:
#                                 #send email to user related listing
#                                 logger.info(f"Failed to found the listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} using this automation after all attempts. Please check your listing manually.")
#                                 logger.info(f"response[1]: {response[1]}")
#                                 logger.info(f"number of retries: {listing.retry_count}")
#                                 logger.info(f"delete the listing andlisting ID {listing.list_id}")
#                                 listing.delete()
#                                 # send_email_to_user(user, listing)
#                                 #------------------------------------------------------------------------
#                                 #---------------------------------------------------------------------------
#                                 #---------------------------------------------------------------------------
#                         elif response[0] == 0:
#                             logger.info(f"Failed to load the facebook page for listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
#                             logger.info(f"response[1]: {response[1]}")
#                             if credentials.retry_count < MAX_RETRIES_ATTEMPTS:
#                                 credentials.retry_count += 1
#                                 credentials.save()
#                                 logger.info(f"Retrying relisting for user {user.email} (Attempt {credentials.retry_count})")
#                             else:
#                                 credentials.status = False
#                                 credentials.save()
#                                 logger.warning(f"Max retry attempts reached. Credentials disabled for user {user.email}")
#                         else:
#                             logger.error(f"Relisting failed for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
#                             logger.info(f"response[1]: {response[1]}")
#                         continue
#                     else:
#                         logging.info(f"Unknown relisting status {getattr(relisting, 'status', None)} for user {listing.user.email} and listing {search_query} and last relisting status {getattr(relisting, 'last_relisting_status', None)}")
#                         logging.info(f"Deleting the listing ID {listing.list_id}")
#                         continue
#                 elif credentials and not listing.is_relist:
#                     time.sleep(random.uniform(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
#                     response = perform_search_and_delete(search_query, price, listed_on, credentials.session_cookie)
#                     if response[0] == 1:
#                         logging.info(f"Deleted relisted Facebook listing for {search_query}")
#                         listing.delete()
#                     elif response[0] == 6:
#                         logger.info("Listing not found for relisting and listing title is {listing.year} {listing.make} {listing.model} for the user {user.email}")
#                         logger.info(f"response[1]: {response[1]}")
#                         listing.delete()
#                     elif response[0] == 4:
#                         #email to me and hussain bhai
#                         logger.info(f"Got issue inside the automation for deleting the relisting {listing.year} {listing.make} {listing.model} and for user {user.email} ")
#                         logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
#                         logger.info(f"response[1]: {response[1]}")
#                         logger.info(f"number of retries: {listing.retry_count}")
#                         if listing.retry_count <= MAX_RETRIES_ATTEMPTS:
#                             listing.retry_count += 1
#                             listing.save()
#                             logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} and number of retries: {listing.retry_count}")
#                         else:
#                             #send email to user related listing
#                             logger.info(f"Failed to found the listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} using this automation after all attempts. Please check your listing manually.")
#                             logger.info(f"response[1]: {response[1]}")
#                             logger.info(f"number of retries: {listing.retry_count}")
#                             logger.info(f"delete the listing andlisting ID {listing.list_id}")
#                             listing.delete()
#                             # send_email_to_user(user, listing)
#                             #------------------------------------------------------------------------
#                             #---------------------------------------------------------------------------
#                             #---------------------------------------------------------------------------
#                     elif response[0] == 0:
#                         logger.info(f"Failed to load the facebook page for listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
#                         logger.info(f"response[1]: {response[1]}")
#                         if credentials.retry_count < MAX_RETRIES_ATTEMPTS:
#                             credentials.retry_count += 1
#                             credentials.save()
#                             logger.info(f"Retrying relisting for user {user.email} (Attempt {credentials.retry_count})")
#                         else:
#                             credentials.status = False
#                             credentials.save()
#                             logger.warning(f"Max retry attempts reached. Credentials disabled for user {user.email}")
#                     else:
#                         logger.error(f"Relisting failed for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
#                         logger.info(f"response[1]: {response[1]}")
#                     continue
#                 else:
#                     logging.info(f"No credentials found for user {listing.user.email}")
#                     continue
#             else:
#                 logging.info(f"Listing ID {listing.list_id} is already exit and marked as {listing.status} and not deleting and status of listing is unknown")
#                 continue
#     else:
#         logging.info("No existing listings found that no exist in the incoming list IDs")
#     logging.info("Completed facebook_profile_listings_thread execution")

# class FacebookProfileListingViewSet(ModelViewSet):
#     """Get all Facebook profile listings"""
#     queryset = FacebookProfileListing.objects.all()
#     serializer_class = FacebookProfileListingSerializer
#     permission_classes = [IsAuthenticated]
#     filter_backends = [filters.SearchFilter]
#     search_fields = ['url']
#     filterset_fields = ['url']
#     ordering_fields = ['url']
#     ordering = ['url']

#     def get_queryset(self):
#         """Get all Facebook profile listings"""
#         if getattr(self, 'swagger_fake_view', False):
#             return FacebookProfileListing.objects.none()
#         user = self.request.user
#         if user.is_superuser:
#             return FacebookProfileListing.objects.all()
#         else:
#             return FacebookProfileListing.objects.filter(user=user).all()
#     def destroy(self, request, *args, **kwargs):
#         """Delete Facebook profile listing"""
#         instance = self.get_object()
#         year_make_model_list=[]
#         session_cookie=None
#         # Proceed with deletion
#         facebook_profile_listing=FacebookProfileListing.objects.filter(id=instance.id).first()
#         logger.info(f"Deleting Facebook profile listing for the user {facebook_profile_listing.user.email} and profile id: {facebook_profile_listing.profile_id}")
#         facebook_profile_vehicle_listings=VehicleListing.objects.filter(facebook_profile=facebook_profile_listing,status="completed").all()
#         if facebook_profile_vehicle_listings:
#             user = facebook_profile_listing.user
#             credentials = FacebookUserCredentials.objects.filter(user=user).first()
#             if not credentials or credentials.session_cookie == {} or not credentials.status:
#                 if credentials:
#                     credentials.status = False
#                     credentials.save()
#                     send_status_reminder_email(credentials)
#                 return JsonResponse({'error': 'Facebook credentials not found, please login again'}, status=404)
#             session_cookie = credentials.session_cookie
#             for current_listing in facebook_profile_vehicle_listings:
#                 temp_list=[]
#                 temp_list.append(current_listing.year + " " + current_listing.make + " " + current_listing.model)
#                 temp_list.append(current_listing.price)
#                 if current_listing.listed_on and not current_listing.is_relist:
#                     listed_on = timezone.localtime(current_listing.listed_on)
#                 else:
#                     relisting=RelistingFacebooklisting.objects.filter(listing=current_listing,status="completed",last_relisting_status=False).first()
#                     if relisting:
#                         listed_on = timezone.localtime(relisting.relisting_date)
#                     else:
#                         listed_on = timezone.localtime(current_listing.listed_on)
#                 temp_list.append(listed_on)
#                 logger.info(f"Adding to year make model temporary list for the user {user.email} and listing title: {current_listing.year} {current_listing.make} {current_listing.model} and price: {current_listing.price} and listed on: {listed_on} which is used to delete the listings")
#                 year_make_model_list.append(temp_list)
#         facebook_profile_listing.delete()
#         if year_make_model_list:
#             # Initialize a queue for the user if it doesn't exist
#             if user.id not in facebook_profile_user_queues:
#                 facebook_profile_user_queues[user.id] = Queue()
#                 threading.Thread(target=delete_facebook_profile_listing_worker, args=(user.id,), daemon=True).start()
#             facebook_profile_user_queues[user.id].put((year_make_model_list,session_cookie))
#         return JsonResponse({'message': 'Listing deleted successfully'}, status=200)

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
        if getattr(self, 'swagger_fake_view', False):
            return GumtreeProfileListing.objects.none()
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

# def delete_multiple_vehicle_listings(year_make_model_list,session_cookie):
#     """Delete multiple vehicle listings"""
#     if session_cookie:
#         while year_make_model_list:
#             current_listing=year_make_model_list.pop(0)
#             search_query = current_listing[0]
#             price=current_listing[1]
#             listing_date=current_listing[2]
#             for i in range(3):
#                 result=perform_search_and_delete(search_query,price,listing_date,session_cookie)
#                 if result[0] == 1:
#                     time.sleep(random.uniform(300,360))
#                     break
#                 elif result[0] == 6:
#                     time.sleep(random.uniform(300, 360))
#                     break
#                 else:
#                     time.sleep(random.uniform(300, 360))
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


# @api_view(['GET'])
# @permission_classes([IsAuthenticated])
# def get_facebook_session_status(request):
#     """Get Facebook session status"""
#     if request.method == 'GET':
#         user = request.user
#         credentials = FacebookUserCredentials.objects.filter(user=user).first()
#         if credentials: 
#             return JsonResponse({'facebook_session_status': credentials.status}, status=200)
#         else:
#             return JsonResponse({'facebook_session_status': False}, status=200)
#     return JsonResponse({'error': 'Invalid request method'}, status=400)

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
        gumtree_profile=gumtree_profile
    ).select_related('gumtree_profile').order_by('-updated_at')

    serializer = VehicleListingSerializer(vehicle_listings, many=True)
    return JsonResponse({
        'count': vehicle_listings.count(),
        'gumtree_profile_url': gumtree_profile_url,
        'results': serializer.data
    }, status=200)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_custom_domain_profile_vehicle_listings(request):
    """Get vehicle listings from specific custom-domain profile URL"""
    user = request.user
    custom_domain_url = request.GET.get('url')

    if not custom_domain_url:
        return JsonResponse({'error': 'url parameter is required'}, status=400)

    custom_domain_profile = CustomDomainProfileListing.objects.filter(
        user=user,
        url=custom_domain_url
    ).first()

    if not custom_domain_profile:
        return JsonResponse({'error': 'Custom domain profile not found or does not belong to user'}, status=404)

    # Match the Gumtree GET endpoint exactly — return all rows for the
    # profile and let the extension handle missing fields (apiflow.md §3.2
    # tells the extension to assume fields are nullable). Filtering by
    # field-completeness here silently hid incomplete rows from publishing.
    vehicle_listings = VehicleListing.objects.filter(
        user=user,
        custom_domain_profile=custom_domain_profile
    ).select_related('custom_domain_profile').order_by('-updated_at')

    serializer = CustomDomainVehicleListingSerializer(vehicle_listings, many=True, context={'request': request})
    return JsonResponse({
        'count': vehicle_listings.count(),
        'custom_domain_url': custom_domain_url,
        'results': serializer.data
    }, status=200)


@csrf_exempt
def custom_domain_image_proxy(request):
    """
    Stream a custom-domain image with permissive CORS so the Chrome extension
    can fetch it from inside the Facebook Marketplace tab. Mirrors the role
    images.gumtree.com.au plays for Gumtree URLs. Allowed only for hosts whose
    adapter declares needs_image_proxy().
    """
    if request.method == "OPTIONS":
        resp = HttpResponse(status=204)
        resp["Access-Control-Allow-Origin"] = "*"
        resp["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp["Access-Control-Allow-Headers"] = "*"
        resp["Access-Control-Max-Age"] = "86400"
        return resp

    if request.method != "GET":
        return HttpResponse(status=405)

    target_url = request.GET.get("url")
    if not target_url or not any_needs_image_proxy(target_url):
        return HttpResponseBadRequest("Invalid or missing url parameter")

    proxy_logger = logging.getLogger('custom_domain')
    try:
        upstream = _http_requests.get(
            target_url,
            timeout=30,
            stream=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                ),
            },
        )
    except _http_requests.RequestException as exc:
        proxy_logger.warning("Custom domain image proxy upstream error for %s: %s", target_url, exc)
        return HttpResponse(status=502)

    if upstream.status_code != 200:
        proxy_logger.warning("Custom domain image proxy non-200 (%s) for %s", upstream.status_code, target_url)
        upstream.close()
        return HttpResponse(status=502)

    content_type = upstream.headers.get("Content-Type", "image/jpeg")
    if not content_type.lower().startswith("image/"):
        proxy_logger.warning("Custom domain image proxy non-image Content-Type %s for %s", content_type, target_url)
        upstream.close()
        return HttpResponse(status=502)

    response = StreamingHttpResponse(
        upstream.iter_content(chunk_size=8192),
        content_type=content_type,
    )
    if upstream.headers.get("Content-Length"):
        response["Content-Length"] = upstream.headers["Content-Length"]
    response["Access-Control-Allow-Origin"] = "*"
    response["Cache-Control"] = "public, max-age=86400"
    return response



# def image_verification(relisting,vehicle_listing):
#     """Verify image upload and update the status of the vehicle listing"""
#     response=image_upload_verification(relisting,vehicle_listing)
#     if relisting:
#         if response[0] == 1:
#             relisting.listing.has_images=True
#             relisting.listing.save()
#         elif response[0] == 0:
#             relisting.status="failed"
#             relisting.save()
#         elif response[0] == 7:
#             if relisting.listing.retry_count < 5:
#                 relisting.listing.retry_count += 1
#                 relisting.listing.save()
#             else:
#                 relisting.listing.status = "sold" 
#                 relisting.listing.save()
#                 relisting.status="completed"
#                 relisting.last_relisting_status=True
#                 relisting.save()
#         else:
#             pass
#     elif vehicle_listing and vehicle_listing.is_relist == True:
#         failed_relisting = RelistingFacebooklisting.objects.filter(listing=vehicle_listing, user=vehicle_listing.user, status__in=["completed", "failed"], last_relisting_status=False).first()
#         if failed_relisting:
#             if response[0] == 1:
#                 failed_relisting.listing.has_images=True
#                 failed_relisting.listing.save()
#             elif response[0] == 0:
#                 failed_relisting.status="failed"
#                 failed_relisting.save()
#             elif response[0] == 7:
#                 if failed_relisting.listing.retry_count < MAX_RETRIES_ATTEMPTS:
#                     failed_relisting.listing.retry_count += 1
#                     failed_relisting.listing.save()
#                 else:
#                     failed_relisting.listing.status = "sold" 
#                     failed_relisting.listing.save()
#                     failed_relisting.status="completed"
#                     failed_relisting.last_relisting_status=True
#                     failed_relisting.save()
#     else:
#         if response[0] == 1:
#             vehicle_listing.has_images=True
#             vehicle_listing.save()
#         elif response[0] == 0:
#             vehicle_listing.status="failed"
#             vehicle_listing.save()
#         elif response[0] == 7:
#             if vehicle_listing.retry_count < 3:
#                 vehicle_listing.retry_count += 1
#                 vehicle_listing.save()
#             else:
#                 vehicle_listing.status = "sold"
#                 vehicle_listing.save()
#         else:
#             pass


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
        is_changed = data.get('is_changed', None)
        
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

        was_first_listing = not vehicle_listing.is_listed

        # Update the listed_on field and track listing/relist counts atomically
        with transaction.atomic():
            vehicle_listing = VehicleListing.objects.select_for_update().get(
                id=vehicle_listing_id, user=request.user
            )
            if not vehicle_listing.is_listed:
                # First time this listing is being marked live — count it
                vehicle_listing.is_listed = True
                vehicle_listing.relist_count = 0

                # Increment user listing_count
                user = request.user
                user.listing_count = F('listing_count') + 1
                user.save(update_fields=['listing_count', 'updated_at'])

                # Sync Subscription.listing_count for invoice overage billing
                try:
                    from payments.models import Subscription as _Subscription
                    sub = _Subscription.objects.select_related('plan').filter(user=user).first()
                    if sub:
                        sub.listing_count = F('listing_count') + 1
                        sub.save(update_fields=['listing_count', 'updated_at'])

                        # Refresh to get real value then check overage
                        user.refresh_from_db(fields=['listing_count'])
                        quota = sub.plan.listing_quota if sub.plan else None
                        if quota and user.listing_count > quota:
                            user.overage_count = F('overage_count') + 1
                            user.save(update_fields=['overage_count', 'updated_at'])
                except Exception:
                    pass  # trial users have no subscription — no overage

            else:
                # Listing already counted — this is a relist
                vehicle_listing.is_relist = True
                vehicle_listing.relist_count = F('relist_count') + 1

                # Increment user relist_cycles
                user = request.user
                user.relist_cycles = F('relist_cycles') + 1
                user.save(update_fields=['relist_cycles', 'updated_at'])

            vehicle_listing.listed_on = listed_on_datetime
            vehicle_listing.status = "completed"
            # A listing that was just successfully (re)published is, by definition, not
            # sold — clear any stale sold state so it doesn't get skipped/misreported later.
            vehicle_listing.sales = False
            vehicle_listing.sold_at = None
            if is_changed is not None:
                vehicle_listing.is_changed = bool(is_changed)
            vehicle_listing.save()

        # NOTE: per-listing overage billing has been RETIRED. Overage is now billed
        # as an active-listings-over-quota metered quantity, reported to Stripe once
        # per period by payments.tasks.report_active_overage_usage (daily beat task).
        # Charging here per first-time publish as well would double-bill, so the old
        # report_listing_overage_metered trigger is intentionally removed. The
        # listing_count increment above is kept for analytics/quota display only.

        # Return success responsea
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


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_vehicle_listing_is_changed(request):
    """
    Flag-only mutation for the `is_changed` field on a VehicleListing.

    This endpoint exists so the browser extension can clear (or set) the
    `is_changed` flag without being forced to also supply a `listed_on` value.
    The existing /listed-on/ endpoint rejects payloads without `listed_on`,
    which caused a flag-clear loop in production (duplicate FB listings).

    Source-agnostic: works for Gumtree and custom-domain listings alike.
    MUST NOT touch is_listed, relist_count, listing_count, relist_cycles,
    Subscription.listing_count, or trigger any overage billing.

    Request Body:
    {
        "id": 123,
        "is_changed": false
    }

    Returns:
    {
        "success": true,
        "data": { "id": 123, "is_changed": false }
    }
    """
    try:
        data = json.loads(request.body)

        if 'id' not in data:
            return JsonResponse({
                'success': False,
                'error': 'Vehicle listing ID is required'
            }, status=400)

        if 'is_changed' not in data:
            return JsonResponse({
                'success': False,
                'error': 'is_changed is required'
            }, status=400)

        vehicle_listing_id = data['id']
        is_changed_raw = data['is_changed']

        # Validate ID
        try:
            vehicle_listing_id = int(vehicle_listing_id)
            if vehicle_listing_id <= 0:
                raise ValueError("ID must be positive")
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Invalid vehicle listing ID format'
            }, status=400)

        # Strict bool — reject ints, strings, etc. to avoid surprise truthiness.
        if not isinstance(is_changed_raw, bool):
            return JsonResponse({
                'success': False,
                'error': 'is_changed must be a boolean (true or false)'
            }, status=400)

        vehicle_listing = VehicleListing.objects.filter(
            id=vehicle_listing_id, user=request.user
        ).first()
        if vehicle_listing is None:
            return JsonResponse({
                'success': False,
                'error': 'Vehicle listing not found or you do not have permission to update it'
            }, status=404)

        # Pure flag mutation — explicit update_fields so nothing else can drift.
        vehicle_listing.is_changed = is_changed_raw
        vehicle_listing.save(update_fields=['is_changed', 'updated_at'])

        return JsonResponse({
            'success': True,
            'data': {
                'id': vehicle_listing.id,
                'is_changed': vehicle_listing.is_changed,
            }
        }, status=200)

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON format in request body'
        }, status=400)

    except Exception as e:
        logger.error(f"Error updating vehicle listing is_changed flag: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'An unexpected error occurred while updating the vehicle listing'
        }, status=500)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def update_vehicle_listing_facebook_id(request):
    """
    Manage the Facebook Marketplace listing ID for a VehicleListing.

    PATCH — store/overwrite the ID (1:1 with VehicleListing — no history kept).
            Used by the extension to enable targeted deletes via the FB ID
            instead of fragile title-search deletes.

        Request Body: { "id": 123, "facebook_listing_id": "1234567890123456" }
        Returns:      { "success": true,
                        "data": { "id": 123, "facebook_listing_id": "1234567890123456" } }

    DELETE — clear the ID back to null (e.g. after the listing is removed from
             Facebook). Idempotent: clearing an already-null field still 200s.

        Request Body: { "id": 123 }
        Returns:      { "success": true,
                        "data": { "id": 123, "facebook_listing_id": null } }

    Every call is logged to the `relister_views` logger — entry, each rejection
    reason, and the final outcome — so we can diagnose why the extension's
    writes do or don't land (the column was at 0/971 populated as of this change).
    """
    # Some clients send DELETE without a Content-Type; tolerate an empty body.
    raw_body = request.body or b''
    logger.info(
        "facebook-id %s by user=%s body_len=%s",
        request.method, getattr(request.user, 'email', request.user), len(raw_body),
    )
    try:
        data = json.loads(raw_body) if raw_body else {}
        if not isinstance(data, dict):
            logger.warning("facebook-id %s rejected: body is not a JSON object", request.method)
            return JsonResponse({
                'success': False,
                'error': 'Request body must be a JSON object'
            }, status=400)

        logger.info(
            "facebook-id %s payload: id=%r (%s) facebook_listing_id=%r (%s)",
            request.method,
            data.get('id'), type(data.get('id')).__name__,
            data.get('facebook_listing_id'), type(data.get('facebook_listing_id')).__name__,
        )

        if 'id' not in data:
            logger.warning("facebook-id %s rejected: 'id' missing", request.method)
            return JsonResponse({
                'success': False,
                'error': 'Vehicle listing ID is required'
            }, status=400)

        # Validate ID
        vehicle_listing_id = data['id']
        try:
            vehicle_listing_id = int(vehicle_listing_id)
            if vehicle_listing_id <= 0:
                raise ValueError("ID must be positive")
        except (ValueError, TypeError):
            logger.warning("facebook-id %s rejected: invalid id %r", request.method, data.get('id'))
            return JsonResponse({
                'success': False,
                'error': 'Invalid vehicle listing ID format'
            }, status=400)

        # DELETE clears the field; PATCH sets it. Resolve the value to store first
        # so the lookup/save tail is shared between both verbs.
        if request.method == 'DELETE':
            facebook_listing_id = None
        else:
            if 'facebook_listing_id' not in data:
                logger.warning("facebook-id PATCH rejected: 'facebook_listing_id' missing (id=%s)", vehicle_listing_id)
                return JsonResponse({
                    'success': False,
                    'error': 'facebook_listing_id is required'
                }, status=400)

            facebook_listing_id = data['facebook_listing_id']

            # facebook_listing_id must be a non-empty string within the model's max_length.
            if not isinstance(facebook_listing_id, str):
                logger.warning(
                    "facebook-id PATCH rejected: facebook_listing_id not a string (id=%s, type=%s)",
                    vehicle_listing_id, type(facebook_listing_id).__name__,
                )
                return JsonResponse({
                    'success': False,
                    'error': 'facebook_listing_id must be a string'
                }, status=400)

            facebook_listing_id = facebook_listing_id.strip()
            if not facebook_listing_id:
                logger.warning("facebook-id PATCH rejected: facebook_listing_id empty (id=%s)", vehicle_listing_id)
                return JsonResponse({
                    'success': False,
                    'error': 'facebook_listing_id must not be empty'
                }, status=400)

            if len(facebook_listing_id) > 128:
                logger.warning("facebook-id PATCH rejected: facebook_listing_id too long (id=%s)", vehicle_listing_id)
                return JsonResponse({
                    'success': False,
                    'error': 'facebook_listing_id exceeds maximum length of 128 characters'
                }, status=400)

        vehicle_listing = VehicleListing.objects.filter(
            id=vehicle_listing_id, user=request.user
        ).first()
        if vehicle_listing is None:
            logger.warning(
                "facebook-id %s 404: listing id=%s not found for user=%s",
                request.method, vehicle_listing_id, getattr(request.user, 'email', request.user),
            )
            return JsonResponse({
                'success': False,
                'error': 'Vehicle listing not found or you do not have permission to update it'
            }, status=404)

        # Overwrite-on-call (PATCH) / clear-to-null (DELETE). Explicit
        # update_fields keeps this surgical and avoids touching scrape data.
        vehicle_listing.facebook_listing_id = facebook_listing_id
        vehicle_listing.save(update_fields=['facebook_listing_id', 'updated_at'])

        logger.info(
            "facebook-id %s ok: listing id=%s facebook_listing_id=%r",
            request.method, vehicle_listing.id, vehicle_listing.facebook_listing_id,
        )
        return JsonResponse({
            'success': True,
            'data': {
                'id': vehicle_listing.id,
                'facebook_listing_id': vehicle_listing.facebook_listing_id,
            }
        }, status=200)

    except json.JSONDecodeError:
        logger.warning("facebook-id %s rejected: invalid JSON body", request.method)
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON format in request body'
        }, status=400)

    except Exception as e:
        logger.error(f"Error updating vehicle listing facebook_listing_id: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': 'An unexpected error occurred while updating the vehicle listing'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_old_vehicle_listings(request):
    """
    Get vehicle listings that are older than specified time based on listed_on date
    
    Query Parameters:
    - days: Integer (default: 7, minimum: 0)
    - hours: Integer (default: 0, range: 0-24)
    - minutes: Integer (default: 0, range: 0-60)
    
    Example: /old-listings/?days=5&hours=12&minutes=30
    """
    user = request.user
    
    try:
        # Get and validate days parameter
        days_str = request.GET.get('days', '7')
        try:
            days = int(days_str)
            if days < 0:
                return JsonResponse({
                    'success': False,
                    'error': 'Days must be 0 or greater'
                }, status=400)
        except ValueError:
            return JsonResponse({
                'success': False,
                'error': 'Days must be a valid integer'
            }, status=400)
        
        # Get and validate hours parameter
        hours_str = request.GET.get('hours', '0')
        try:
            hours = int(hours_str)
            if hours < 0 or hours > 24:
                return JsonResponse({
                    'success': False,
                    'error': 'Hours must be between 0 and 24'
                }, status=400)
        except ValueError:
            return JsonResponse({
                'success': False,
                'error': 'Hours must be a valid integer'
            }, status=400)
        
        # Get and validate minutes parameter
        minutes_str = request.GET.get('minutes', '0')
        try:
            minutes = int(minutes_str)
            if minutes < 0 or minutes > 60:
                return JsonResponse({
                    'success': False,
                    'error': 'Minutes must be between 0 and 60'
                }, status=400)
        except ValueError:
            return JsonResponse({
                'success': False,
                'error': 'Minutes must be a valid integer'
            }, status=400)
        
        # Calculate the cumulative cutoff datetime
        cutoff_datetime = timezone.now() - timedelta(days=days, hours=hours, minutes=minutes)
        
        # Get user's vehicle listings older than the cutoff datetime
        vehicle_listings = VehicleListing.objects.filter(
            status="completed",
            listed_on__lte=cutoff_datetime,
            is_relist=False,
            sales=False,
            user=user
        ).order_by("listed_on")
        
        # Serialize the listings using the existing serializer. Request context
        # is required so DNA image URLs get rewritten to the proxy — the extension
        # consumes this `images` array directly (publishListing.ts uploads them
        # to Facebook from the Marketplace tab, which fails CORS on raw DNA URLs).
        # Gumtree URLs are allowlisted in any_needs_image_proxy and pass through.
        serializer = VehicleListingSerializer(vehicle_listings, many=True, context={'request': request})
        
        return JsonResponse({
            'count': vehicle_listings.count(),
            'cutoff_applied': {
                'days': days,
                'hours': hours,
                'minutes': minutes,
                'cutoff_datetime': cutoff_datetime.isoformat()
            },
            'results': serializer.data
        }, status=200)
        
    except Exception as e:
        logger.error(f"Error retrieving old vehicle listings: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'An unexpected error occurred while retrieving old vehicle listings: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def verify_listing_active(request):
    """
    Live, single-listing check called by the extension immediately before it relists a
    vehicle — closes the staleness window left by the twice-daily Gumtree profile
    re-scrape (a car sold between scrapes would otherwise still show sales=False).

    Query Parameters:
    - id: VehicleListing primary key (required)

    Returns:
    {"success": true, "active": true|false, "indeterminate": false}
    - active=false  -> listing confirmed gone from Gumtree; backend has just marked it
                       sold (status="sold", sales=True). Caller must not relist it.
    - active=true   -> confirmed still live on Gumtree; sales/sold_at cleared if stale.
    - indeterminate -> Gumtree/ZenRows could not be reached; no state was changed.
                       Caller should skip this cycle and retry later rather than guess.
    """
    listing_id = request.GET.get('id')
    if not listing_id:
        return JsonResponse({'success': False, 'error': 'id parameter is required'}, status=400)

    try:
        listing_id = int(listing_id)
    except ValueError:
        return JsonResponse({'success': False, 'error': 'id must be an integer'}, status=400)

    listing = VehicleListing.objects.filter(id=listing_id, user=request.user).first()
    if not listing:
        return JsonResponse({'success': False, 'error': 'Vehicle listing not found'}, status=404)

    # Only rows actually sourced from a Gumtree profile scrape have a list_id that
    # means anything to Gumtree's API. Custom-domain rows also populate list_id (with
    # an internal identifier from that scraper), so checking list_id truthiness alone
    # would run a bogus "Gumtree existence" lookup against it and could misreport a
    # perfectly live custom-domain listing as sold. Gate on the actual source instead.
    if not listing.gumtree_profile_id or not listing.list_id:
        return JsonResponse({'success': True, 'active': None, 'indeterminate': True})

    result = is_gumtree_listing_active(listing.list_id)

    if result is False:
        if listing.status != "sold":
            mark_listing_sold(listing)
        return JsonResponse({'success': True, 'active': False, 'indeterminate': False})

    if result is True:
        if listing.sales or listing.sold_at or listing.status == "sold":
            listing.status = "completed"
            listing.sales = False
            listing.sold_at = None
            listing.save(update_fields=['status', 'sales', 'sold_at', 'updated_at'])
        return JsonResponse({'success': True, 'active': True, 'indeterminate': False})

    # result is None — ZenRows/network failure. Don't mutate anything.
    logger.warning(f"verify_listing_active: indeterminate result for listing {listing_id} (list_id={listing.list_id})")
    return JsonResponse({'success': True, 'active': None, 'indeterminate': True})


@api_view(['GET'])
@permission_classes([AllowAny])
def get_all_products(request):
    """
    Public, unauthenticated product list for storefront consumption.

    Returns only what a listing card needs (id, name, image, price) for every
    vehicle that has actually been published, newest first.

    Query Parameters:
    - limit: Integer (default: 20, max: 100) — page size
    - offset: Integer (default: 0) — rows to skip
    """
    try:
        limit = min(int(request.GET.get('limit', 20)), 100)
        offset = max(int(request.GET.get('offset', 0)), 0)
    except ValueError:
        return JsonResponse({'error': 'limit and offset must be integers'}, status=400)

    products = VehicleListing.objects.filter(is_listed=True).order_by('-updated_at')
    total_count = products.count()
    page = products[offset:offset + limit]

    serializer = ProductListSerializer(page, many=True)
    return JsonResponse({
        'count': total_count,
        'limit': limit,
        'offset': offset,
        'results': serializer.data
    }, status=200)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_products_by_category(request, category):
    """
    Public, unauthenticated product list filtered by category, e.g.
    GET /api/vehicle-listing/categories/toyota/

    "Category" maps to the vehicle's make/brand (Toyota, Honda, Ford, etc.)
    to power the "Browse by Brand" section on the storefront. Matching is
    case-insensitive so "toyota", "Toyota", and "TOYOTA" all resolve.
    Returns every matching row, unpaginated.
    """
    products = VehicleListing.objects.filter(
        is_listed=True,
        make__iexact=category
    ).order_by('-updated_at')

    if not products.exists():
        return JsonResponse({
            'message': 'This category not found',
            'results': []
        }, status=200)

    serializer = ProductListSerializer(products, many=True)
    return JsonResponse({
        'count': products.count(),
        'results': serializer.data
    }, status=200)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_product_by_slug(request, name, vehicle_id):
    """
    Public single-product lookup by SEO-friendly slug: <name>-<id>,
    e.g. "toyota-corolla-2019-100". The URL converters in urls.py already
    split this into `name` (everything before the last hyphen) and
    `vehicle_id` (the trailing integer) — `name` is decorative and never
    validated against the row's actual make/model/year.
    """
    product = VehicleListing.objects.select_related('user').filter(pk=vehicle_id, is_listed=True).first()
    if not product:
        return JsonResponse({'error': 'Product not found'}, status=404)

    serializer = ProductDetailSerializer(product, context={'request': request})
    return JsonResponse(serializer.data, status=200)


@api_view(['POST'])
@permission_classes([AllowAny])
def increment_product_view_count(request, vehicle_id):
    """
    Public, unauthenticated view-count increment, called once per product
    page visit, e.g. POST /api/vehicle-listing/vehicle/100/increment-view/

    Uses an F() expression so concurrent visits increment atomically at the
    database level instead of racing on a read-modify-write in Python.
    """
    updated = VehicleListing.objects.filter(
        pk=vehicle_id,
        is_listed=True
    ).update(total_view_count=F('total_view_count') + 1)

    if not updated:
        return JsonResponse({'error': 'Product not found'}, status=404)

    total_view_count = VehicleListing.objects.filter(pk=vehicle_id).values_list(
        'total_view_count', flat=True
    ).first()
    return JsonResponse({'total_view_count': total_view_count}, status=200)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_latest_arrivals(request):
    """
    Public, unauthenticated list of the 4 most recently added vehicles,
    e.g. GET /api/vehicle-listing/latest-arrivals/
    """
    products = VehicleListing.objects.filter(is_listed=True).order_by('-created_at')[:4]

    serializer = ProductListSerializer(products, many=True)
    return JsonResponse({'results': serializer.data}, status=200)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_popular_vehicles(request):
    """
    Public, unauthenticated list of the 4 most-viewed vehicles, e.g.
    GET /api/vehicle-listing/popular-vehicles/
    """
    products = VehicleListing.objects.filter(is_listed=True).order_by('-total_view_count', '-created_at')[:4]

    serializer = ProductListSerializer(products, many=True)
    return JsonResponse({'results': serializer.data}, status=200)


# Numeric-range params: (query param prefix, model field, cast target field name).
# price/year are stored as CharField (scrapers normalize them to plain digit
# strings, e.g. "45000", but the column itself has no numeric constraint) so a
# min/max range needs an explicit Cast rather than a plain __gte/__lte, which
# would otherwise compare lexicographically. The regex guard excludes any
# legacy/dirty non-digit values instead of letting the Cast error out.
_NUMERIC_RANGE_FIELDS = {
    'year': 'year',
    'mileage': 'mileage',
    'price': 'price',
}
# Fields matched with an exact (case-insensitive) equality check per value —
# these are dropdown-style filters with a small, clean set of values in the
# data (e.g. transmission is always "Automatic"/"Manual"), so the frontend
# sends one of a known value (repeat/comma-separate the param for an OR match).
_EXACT_MULTI_FIELDS = ['transmission', 'body_type']
# Fields matched with a partial (case-insensitive) substring check per value —
# these carry free-text/compound values in the data (e.g. model is a full
# trim string like "Outlander ES ZL", fuel_type is "Petrol - Unleaded"), so an
# exact match would almost never hit; substring match is what users expect
# when typing a make/model/colour into a search box.
_PARTIAL_MULTI_FIELDS = ['make', 'model', 'fuel_type', 'color', 'location']

ORDERING_OPTIONS = {
    'newest': '-updated_at',
    'price_asc': 'price_int',
    'price_desc': '-price_int',
    'mileage_asc': 'mileage',
    'mileage_desc': '-mileage',
    'year_asc': 'year_int',
    'year_desc': '-year_int',
}


@api_view(['GET'])
@permission_classes([AllowAny])
def search_products(request):
    """
    Public, unauthenticated vehicle search with multi-field filtering, e.g.
    GET /api/vehicle-listing/search/?make=Toyota&transmission=Automatic&price_max=30000

    All parameters are optional and combined with AND. Comma-separate a
    value to OR within that parameter, e.g. make=Toyota,Honda.

    Query Parameters:
    - name: Text — matches make, model, or variant (case-insensitive, partial)
    - transmission, body_type: exact match (case-insensitive), comma-separated
      for multiple values, e.g. transmission=Automatic
    - make, model, fuel_type, color, location: partial match
      (case-insensitive), comma-separated for multiple values
    - year: exact year(s), comma-separated for multiple values
    - year_min, year_max: inclusive year range
    - mileage_min, mileage_max: inclusive mileage (km) range
    - price_min, price_max: inclusive price range
    - ordering: one of newest (default), price_asc, price_desc, mileage_asc,
      mileage_desc, year_asc, year_desc
    - limit: Integer (default 20, max 100) — page size
    - offset: Integer (default 0) — rows to skip
    """
    params = request.GET
    errors = {}

    def multi_values(param_name):
        raw = params.get(param_name)
        if not raw:
            return None
        return [v.strip() for v in raw.split(',') if v.strip()]

    def parse_int(param_name):
        raw = params.get(param_name)
        if raw in (None, ''):
            return None
        try:
            return int(raw)
        except ValueError:
            errors[param_name] = 'must be an integer'
            return None

    products = VehicleListing.objects.filter(is_listed=True)

    name = params.get('name')
    if name:
        products = products.filter(
            Q(make__icontains=name) | Q(model__icontains=name) | Q(variant__icontains=name)
        )

    for field in _EXACT_MULTI_FIELDS:
        values = multi_values(field)
        if values:
            match = Q()
            for value in values:
                match |= Q(**{f'{field}__iexact': value})
            products = products.filter(match)

    for field in _PARTIAL_MULTI_FIELDS:
        values = multi_values(field)
        if values:
            match = Q()
            for value in values:
                match |= Q(**{f'{field}__icontains': value})
            products = products.filter(match)

    years = multi_values('year')
    if years:
        match = Q()
        for year in years:
            match |= Q(year__iexact=year)
        products = products.filter(match)

    range_filters = {
        'year': (parse_int('year_min'), parse_int('year_max')),
        'mileage': (parse_int('mileage_min'), parse_int('mileage_max')),
        'price': (parse_int('price_min'), parse_int('price_max')),
    }

    needs_year_cast = any(range_filters['year'])
    needs_price_cast = any(range_filters['price'])
    if needs_year_cast:
        products = products.filter(year__regex=r'^\d+$').annotate(
            year_int=Cast('year', output_field=IntegerField())
        )
    if needs_price_cast:
        products = products.filter(price__regex=r'^\d+$').annotate(
            price_int=Cast('price', output_field=IntegerField())
        )

    range_min, range_max = range_filters['year']
    if range_min is not None:
        products = products.filter(year_int__gte=range_min)
    if range_max is not None:
        products = products.filter(year_int__lte=range_max)

    range_min, range_max = range_filters['mileage']
    if range_min is not None:
        products = products.filter(mileage__gte=range_min)
    if range_max is not None:
        products = products.filter(mileage__lte=range_max)

    range_min, range_max = range_filters['price']
    if range_min is not None:
        products = products.filter(price_int__gte=range_min)
    if range_max is not None:
        products = products.filter(price_int__lte=range_max)

    ordering = params.get('ordering', 'newest')
    if ordering not in ORDERING_OPTIONS:
        errors['ordering'] = f"must be one of {', '.join(ORDERING_OPTIONS)}"
    elif ordering in ('price_asc', 'price_desc') and not needs_price_cast:
        products = products.filter(price__regex=r'^\d+$').annotate(
            price_int=Cast('price', output_field=IntegerField())
        )
    elif ordering in ('year_asc', 'year_desc') and not needs_year_cast:
        products = products.filter(year__regex=r'^\d+$').annotate(
            year_int=Cast('year', output_field=IntegerField())
        )

    limit = parse_int('limit')
    offset = parse_int('offset')

    if errors:
        return JsonResponse({'errors': errors}, status=400)

    limit = min(limit, 100) if limit is not None else 20
    offset = max(offset, 0) if offset is not None else 0

    products = products.order_by(ORDERING_OPTIONS[ordering])
    total_count = products.count()
    page = products[offset:offset + limit]

    serializer = ProductListSerializer(page, many=True)
    return JsonResponse({
        'count': total_count,
        'limit': limit,
        'offset': offset,
        'results': serializer.data,
    }, status=200)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_top_dealers(request):
    """
    Public, unauthenticated list of the top 4 dealers/sellers by listing
    count, e.g. GET /api/vehicle-listing/top-dealers/

    Returns each dealer's name, address, and how many vehicles they
    currently have listed.
    """
    dealers = User.objects.filter(
        is_approved=True, is_active=True
    ).annotate(
        active_listing_count=Count('vehiclelisting', filter=Q(vehiclelisting__is_listed=True))
    ).order_by('-active_listing_count', 'dealership_name')[:4]

    serializer = DealerListSerializer(dealers, many=True)
    return JsonResponse({'results': serializer.data}, status=200)


# ──────────────────────────────────────────────────────────────────────────────
# Facebook listing snapshot — pushed hourly by the extension, read by the admin
# dashboard (gumtree + custom-domain modes). See VehicleListing.models.FacebookListingSnapshot.
# ──────────────────────────────────────────────────────────────────────────────

def _parse_dt(value):
    """Accept ISO-8601 strings OR unix seconds (int/float/str) → aware datetime."""
    if value in (None, ''):
        return None
    # Python's datetime.timezone.utc — django.utils.timezone.utc was removed in Django 5.0.
    from datetime import timezone as _dt_timezone
    try:
        # Unix seconds (Facebook creationTime is seconds since epoch)
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.replace('.', '', 1).isdigit()):
            return datetime.fromtimestamp(float(value), tz=_dt_timezone.utc)
        # ISO-8601 (allow trailing 'Z')
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError, OSError):
        return None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_facebook_listing_snapshot(request):
    """
    Replace the authenticated user's Facebook-listing snapshot with the set the
    extension just observed. Called ~hourly while the extension runs.

    Request Body:
    {
      "mode": "gumtree" | "customdomain",
      "listings": [
        {
          "fb_listing_id": "1234567890",
          "fb_url": "https://www.facebook.com/marketplace/item/1234567890/",
          "title": "2019 Nissan Navara ST",
          "price": "35490",
          "fb_published_at": 1784321532,          # unix seconds OR ISO-8601
          "days_on_facebook": 14,                  # optional; computed if omitted
          "matched_listing_id": 7945,              # backend VehicleListing id or null
          "is_aged": true,
          "is_duplicate": false,
          "duplicate_count": 1
        }, ...
      ]
    }
    Returns: { "success": true, "saved": <n>, "synced_at": <iso> }
    """
    user = request.user
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    mode = data.get('mode')
    # `listings` is now OPTIONAL: the extension pushes status-only syncs when
    # Facebook can't be loaded (verification wall / not logged in), so a dealer
    # still reports in and shows on the dashboard with their error. Only replace
    # the FB set when a listings array is actually provided.
    listings = data.get('listings')
    listings_present = isinstance(listings, list)

    # Per-sync status (drives the dashboard's "which dealers are broken" view).
    VALID_STATUSES = {c[0] for c in ExtensionSyncStatus.STATUS_CHOICES}
    status = data.get('status')
    status = status if status in VALID_STATUSES else 'ok'
    status_detail = data.get('status_detail') or None
    extension_version = data.get('extension_version') or None

    now = timezone.now()
    # Cache this user's VehicleListing ids so a bogus/foreign matched_listing_id can't
    # attach a snapshot to another user's listing.
    own_listing_ids = set(
        VehicleListing.objects.filter(user=user).values_list('id', flat=True)
    )

    rows = []
    for item in (listings if listings_present else []):
        if not isinstance(item, dict):
            continue
        fb_id = str(item.get('fb_listing_id') or '').strip()
        if not fb_id:
            continue
        published = _parse_dt(item.get('fb_published_at'))
        days = item.get('days_on_facebook')
        if days is None and published is not None:
            days = max(0, (now - published).days)
        matched_id = item.get('matched_listing_id')
        matched_id = matched_id if matched_id in own_listing_ids else None
        rows.append(FacebookListingSnapshot(
            user=user,
            fb_listing_id=fb_id,
            fb_url=(item.get('fb_url') or None),
            title=(item.get('title') or None),
            price=(str(item.get('price')) if item.get('price') is not None else None),
            fb_published_at=published,
            days_on_facebook=days,
            matched_listing_id=matched_id,
            is_aged=bool(item.get('is_aged')),
            is_duplicate=bool(item.get('is_duplicate')),
            duplicate_count=int(item.get('duplicate_count') or 1),
            mode=(mode if mode in ('gumtree', 'customdomain') else None),
        ))

    # Backend listings NOT on Facebook + the exact reason (optional array —
    # older extension builds won't send it, so treat a missing/invalid value as
    # "don't touch the unpublished set" only when the key is entirely absent;
    # an explicit empty list means "no unpublished listings", so clear the set).
    unpublished_in = data.get('unpublished')
    unpublished_present = 'unpublished' in data and isinstance(unpublished_in, list)
    VALID_REASONS = {c[0] for c in UnpublishedListingSnapshot.REASON_CHOICES}
    unpublished_rows = []
    if unpublished_present:
        for item in unpublished_in:
            if not isinstance(item, dict):
                continue
            lid = item.get('listing_id')
            lid = lid if lid in own_listing_ids else None
            reason = item.get('reason')
            reason = reason if reason in VALID_REASONS else None
            unpublished_rows.append(UnpublishedListingSnapshot(
                user=user,
                listing_id=lid,
                title=(item.get('title') or None),
                price=(str(item.get('price')) if item.get('price') is not None else None),
                images_count=int(item.get('images_count') or 0),
                reason=reason,
                reason_detail=(item.get('reason_detail') or None),
                mode=(mode if mode in ('gumtree', 'customdomain') else None),
            ))

    # Whole-set replace for this user (atomic). FB / unpublished sets are only
    # touched when their arrays were provided, so a status-only sync (FB failed)
    # leaves the last-known-good listings intact and just updates the status.
    with transaction.atomic():
        if listings_present:
            FacebookListingSnapshot.objects.filter(user=user).delete()
            if rows:
                FacebookListingSnapshot.objects.bulk_create(rows, batch_size=500)
        if unpublished_present:
            UnpublishedListingSnapshot.objects.filter(user=user).delete()
            if unpublished_rows:
                UnpublishedListingSnapshot.objects.bulk_create(unpublished_rows, batch_size=500)

        # Heartbeat/status row — upserted on EVERY sync so the dealer always shows.
        fb_count = len(rows) if listings_present else FacebookListingSnapshot.objects.filter(user=user).count()
        unpub_count = len(unpublished_rows) if unpublished_present else UnpublishedListingSnapshot.objects.filter(user=user).count()
        ExtensionSyncStatus.objects.update_or_create(
            user=user,
            defaults={
                'mode': (mode if mode in ('gumtree', 'customdomain') else None),
                'status': status,
                'status_detail': status_detail,
                'fb_count': fb_count,
                'unpublished_count': unpub_count,
                'extension_version': extension_version,
            },
        )

    logger.info(
        "fb-snapshot sync user=%s mode=%s status=%s saved=%s unpublished=%s",
        getattr(user, 'email', user), mode, status,
        len(rows) if listings_present else 'n/a',
        len(unpublished_rows) if unpublished_present else 'n/a',
    )
    return JsonResponse({
        'success': True,
        'saved': len(rows) if listings_present else None,
        'unpublished_saved': len(unpublished_rows) if unpublished_present else None,
        'status': status,
        'synced_at': now.isoformat(),
    }, status=200)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def get_facebook_listing_snapshots(request):
    """
    ADMIN ONLY — Facebook-listing snapshot grouped by dealer, with a per-user
    summary, SERVER-PAGINATED by dealer for the admin dashboard.

    Query params (all optional):
      - page:        1-based page of dealers (default 1)
      - page_size:   dealers per page (default 10, max 100)
      - user_id:     restrict to one dealer
      - aged=1:      only aged listings (and dealers that have some)
      - duplicates=1:only duplicate listings
      - min_days:    only listings that have been on Facebook >= N days
      - search:      match dealer email / contact name / dealership name
    """
    # Filtered base set of listing rows.
    base = FacebookListingSnapshot.objects.all()
    user_id = request.GET.get('user_id')
    if user_id:
        base = base.filter(user_id=user_id)
    if request.GET.get('aged') in ('1', 'true', 'True'):
        base = base.filter(is_aged=True)
    if request.GET.get('duplicates') in ('1', 'true', 'True'):
        base = base.filter(is_duplicate=True)
    try:
        min_days = int(request.GET.get('min_days', ''))
    except (ValueError, TypeError):
        min_days = None
    if min_days and min_days > 0:
        base = base.filter(days_on_facebook__gte=min_days)
    search = (request.GET.get('search') or '').strip()
    if search:
        base = base.filter(
            Q(user__email__icontains=search) |
            Q(user__contact_person_name__icontains=search) |
            Q(user__dealership_name__icontains=search)
        )

    # Which dealers to list: ALL that have ever synced (ExtensionSyncStatus), so
    # dealers with Facebook errors / zero listings still appear (with their
    # status). When a listing-level filter is active (aged/duplicates/min_days),
    # restrict to dealers that actually have matching Facebook rows.
    listing_filters_active = (
        request.GET.get('aged') in ('1', 'true', 'True')
        or request.GET.get('duplicates') in ('1', 'true', 'True')
        or bool(min_days and min_days > 0)
    )
    # Status rows (search/user filtered) — source of truth for "broken" dealers.
    status_qs = ExtensionSyncStatus.objects.select_related('user')
    if user_id:
        status_qs = status_qs.filter(user_id=user_id)
    if search:
        status_qs = status_qs.filter(
            Q(user__email__icontains=search) |
            Q(user__contact_person_name__icontains=search) |
            Q(user__dealership_name__icontains=search)
        )
    status_map = {s.user_id: s for s in status_qs}

    # Per-dealer FB summary (from the already search/filter-narrowed `base`) — used
    # for counts + ordering AND to include dealers whose extension hasn't sent a
    # status yet (older build) so nothing disappears during rollout.
    fb_summary = {
        row['user_id']: row
        for row in base.values('user_id').annotate(
            aged=Count('id', filter=Q(is_aged=True)),
            last_synced=Max('synced_at'),
        )
    }

    # Candidate dealers = union of (ever-synced) + (has FB rows) + (has unpublished),
    # unless a listing-level filter is active (then only dealers with matching rows).
    if listing_filters_active:
        candidate_ids = set(fb_summary.keys())
    else:
        candidate_ids = set(status_map.keys()) | set(fb_summary.keys())
        up_q = UnpublishedListingSnapshot.objects.all()
        if user_id:
            up_q = up_q.filter(user_id=user_id)
        if search:
            up_q = up_q.filter(
                Q(user__email__icontains=search) |
                Q(user__contact_person_name__icontains=search) |
                Q(user__dealership_name__icontains=search)
            )
        candidate_ids |= set(up_q.values_list('user_id', flat=True).distinct())

    # Order: broken dealers (status != ok) first, then most-aged, then most-recent.
    def _sort_key(uid):
        st = status_map.get(uid)
        err = 0 if (st and st.status and st.status != 'ok') else 1
        aged = fb_summary.get(uid, {}).get('aged', 0) or 0
        synced = (st.synced_at if st else None) or fb_summary.get(uid, {}).get('last_synced')
        return (err, -aged, -(synced.timestamp() if synced else 0))
    ordered_ids = sorted(candidate_ids, key=_sort_key)

    total_users = len(ordered_ids)
    try:
        page = max(1, int(request.GET.get('page', '1')))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = min(100, max(1, int(request.GET.get('page_size', '10'))))
    except (ValueError, TypeError):
        page_size = 10
    total_pages = max(1, (total_users + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_user_ids = ordered_ids[start:start + page_size]
    users_by_id = {u.id: u for u in User.objects.filter(id__in=page_user_ids)}

    # Fetch listing rows only for the dealers on this page.
    listings_by_user = {}
    if page_user_ids:
        rows = (
            base.filter(user_id__in=page_user_ids)
            .select_related('matched_listing')
            .order_by('user_id', 'fb_published_at')
        )
        for s in rows:
            listings_by_user.setdefault(s.user_id, []).append(s)

    # Unpublished (not-on-Facebook) listings for the dealers on this page.
    unpublished_by_user = {}
    if page_user_ids:
        up_rows = (
            UnpublishedListingSnapshot.objects.filter(user_id__in=page_user_ids)
            .select_related('listing')
            .order_by('user_id', 'reason', 'title')
        )
        for u in up_rows:
            unpublished_by_user.setdefault(u.user_id, []).append(u)

    results = []
    for uid in page_user_ids:
        st = status_map.get(uid)
        user_obj = users_by_id.get(uid) or (st.user if st else None)
        rows = listings_by_user.get(uid, [])
        last_synced = (st.synced_at if st else None) or (rows[0].synced_at if rows else None)
        results.append({
            'user_id': uid,
            'email': getattr(user_obj, 'email', None),
            'name': (getattr(user_obj, 'contact_person_name', None) or getattr(user_obj, 'dealership_name', None)),
            'mode': (st.mode if st else None) or (rows[0].mode if rows else None),
            'last_synced_at': last_synced.isoformat() if last_synced else None,
            'status': st.status if st else 'ok',
            'status_detail': st.status_detail if st else None,
            'extension_version': st.extension_version if st else None,
            'total': len(rows),
            'aged': sum(1 for s in rows if s.is_aged),
            'duplicates': sum(1 for s in rows if s.is_duplicate),
            'matched': sum(1 for s in rows if s.matched_listing_id),
            'listings': [{
                'fb_listing_id': s.fb_listing_id,
                'fb_url': s.fb_url,
                'title': s.title,
                'price': s.price,
                'fb_published_at': s.fb_published_at.isoformat() if s.fb_published_at else None,
                'days_on_facebook': s.days_on_facebook,
                'is_aged': s.is_aged,
                'is_duplicate': s.is_duplicate,
                'duplicate_count': s.duplicate_count,
                'matched_listing_id': s.matched_listing_id,
                'matched_listing': (
                    f"{s.matched_listing.year or ''} {s.matched_listing.make or ''} {s.matched_listing.model or ''}".strip()
                    if s.matched_listing_id else None
                ),
                # Source listing URL (Gumtree profile listing / custom-domain detail page)
                # and the Django-admin change page for that backend row.
                'matched_listing_url': (s.matched_listing.url if s.matched_listing_id else None),
                'matched_listing_admin_url': (
                    request.build_absolute_uri(f'/admin/VehicleListing/vehiclelisting/{s.matched_listing_id}/change/')
                    if s.matched_listing_id else None
                ),
                'mode': s.mode,
                'synced_at': s.synced_at.isoformat() if s.synced_at else None,
            } for s in rows],
            'unpublished_count': len(unpublished_by_user.get(uid, [])),
            'unpublished': [{
                'listing_id': u.listing_id,
                'title': u.title,
                'price': u.price,
                'images_count': u.images_count,
                'reason': u.reason,
                'reason_detail': u.reason_detail,
                'source_url': (u.listing.url if u.listing_id else None),
                'admin_url': (
                    request.build_absolute_uri(f'/admin/VehicleListing/vehiclelisting/{u.listing_id}/change/')
                    if u.listing_id else None
                ),
                'synced_at': u.synced_at.isoformat() if u.synced_at else None,
            } for u in unpublished_by_user.get(uid, [])],
        })

    return JsonResponse({
        'success': True,
        'users': results,
        'page': page,
        'page_size': page_size,
        'total_users': total_users,
        'total_pages': total_pages,
    }, status=200)