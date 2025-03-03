from .gumtree_scraper import get_listings,get_gumtree_listings
from .url_importer import ImportFromUrl
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework import filters
from .serializers import VehicleListingSerializer, ListingUrlSerializer, FacebookUserCredentialsSerializer,FacebookProfileListingSerializer,GumtreeProfileListingSerializer
from accounts.models import User
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookListing,GumtreeProfileListing,FacebookProfileListing
import json
from .facebook_listing import create_marketplace_listing,login_to_facebook, perform_search_and_delete, get_facebook_profile_listings, extract_facebook_listing_details,extract_facebook_listing_details
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import threading
from rest_framework.decorators import api_view, permission_classes
import time
import random   
from datetime import datetime, timedelta

@csrf_exempt
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
        
        import_url = ImportFromUrl(url)
        is_valid, error_message = import_url.validate()

        if not is_valid:
            return JsonResponse({'error': error_message}, status=200)
        list_id = extract_seller_id(url)
        if not list_id or not list_id.isdigit():
            return JsonResponse({'error': 'Invalid seller ID'}, status=200)
        if ListingUrl.objects.filter(url=url,user=user).exists():
            return JsonResponse({'error': 'URL already exists'}, status=200)
        if import_url.print_url_type() == "Facebook Profile" or import_url.print_url_type() == "Gumtree Profile":
            return  JsonResponse({'error': 'This is Facebook Profile Url, Now, Only Process the Gumtree and Facebook single Url'}, status=200)
        if import_url.print_url_type() == "Facebook":
            # Extract facebook listing data from URL
            current_listing = {}
            current_listing['url'] = url
            current_listing['mileage'] = None
            credentials = FacebookUserCredentials.objects.filter(user=user).first()
            if credentials and credentials.session_cookie != {}:
                import_url_instance = ListingUrl.objects.create(url=url, user=user , status='pending')
                response = extract_facebook_listing_details(current_listing,credentials.session_cookie)
                if response and response.get("year") and response.get("make") and response.get("model"):
                    vehicle_listing = VehicleListing.objects.create(
                        user=user,
                        list_id=list_id,
                        year=response["year"],
                        body_type="Other",
                        fuel_type="Other",
                        color="Other",
                        variant="Other",
                        make=response["make"],
                        mileage=current_listing["mileage"],
                        model=response["model"],
                        price=response.get("price"),
                        transmission=None,
                        description=response.get("description"),
                        images=response["images"],
                        url=current_listing["url"],
                        location=response.get("location"),
                        status="pending",
                    )
                    import_url_instance.status = "completed"
                    import_url_instance.save()
                    thread = threading.Thread(target=create_facebook_listing, args=(vehicle_listing,))
                    thread.start()
                    user_data = {
                        'url': url,
                        'message': "Extracted data successfully and listing created in facebook is in progress"
                    }
                    return JsonResponse(user_data, status=200)
                else:
                    import_url_instance.status = "failed"
                    import_url_instance.save()
                    return JsonResponse({'error': 'Failed to extract facebook listing details, Please check the URL and try again'}, status=200)
            elif credentials and credentials.session_cookie == {}:
                import_url_instance = ListingUrl.objects.create(url=url, user=user , status='pending')
                session_cookie =login_to_facebook(credentials.email, credentials.password)
                if session_cookie:
                    credentials.session_cookie = session_cookie
                    credentials.save()
                    import_url_instance = ListingUrl.objects.create(url=url, user=user , status='pending')
                    response = extract_facebook_listing_details(current_listing,credentials.session_cookie)
                    if response and response.get("year") and response.get("make") and response.get("model"):
                        vehicle_listing = VehicleListing.objects.create(
                            user=user,
                            list_id=list_id,
                            year=response["year"],
                            body_type="Other",
                            fuel_type="Other",
                            color="Other",
                            variant="Other",
                            make=response["make"],
                            mileage=current_listing["mileage"],
                            model=response["model"],
                            price=response.get("price"),
                            transmission=None,
                            description=response.get("description"),
                            images=response["images"],
                            url=current_listing["url"],
                            location=response.get("location"),
                            status="pending",
                        )
                        import_url_instance.status = "completed"
                        import_url_instance.save()
                        thread = threading.Thread(target=create_facebook_listing, args=(vehicle_listing,))
                        thread.start()
                        user_data = {
                            'url': url,
                            'message': "Extracted data successfully and listing created in facebook is in progress"
                        }
                        return JsonResponse(user_data, status=200)
                    else:
                        import_url_instance.status = "failed"
                        import_url_instance.save()
                        return JsonResponse({'error': 'Failed to extract facebook listing details, Please check the URL and try again'}, status=200)

                else:
                    import_url_instance.status = "failed"
                    import_url_instance.save()
                    return JsonResponse({'error': 'Failed to extract facebook listing details, Facebook login failed. please check the credentials and try again'}, status=200)
            else:
                return JsonResponse({'error': 'Failed to extract facebook listing details, Please Provide the Facebook credentials and try again'}, status=200)
        # Extract Gumtree data from URL
        import_url_instance = ListingUrl.objects.create(url=url, user=user , status='pending')
        vehicle_listing = get_listings(url,user,import_url_instance)
        if vehicle_listing:
            vls = VehicleListingSerializer(vehicle_listing)
            print(f"vehicle_listing: {vehicle_listing}")
            thread = threading.Thread(target=create_facebook_listing, args=(vehicle_listing,))
            thread.start()
            # Prepare user related listing data
            user_data = {
                'url': url,
                'message': "Extracted data successfully and listing created in facebook is in progress"
            }
            return JsonResponse(user_data, status=200)
        else:
            import_url_instance.status = "failed"
            import_url_instance.save()
            return JsonResponse({'error': 'Failed to extract data from URL, Check the URL and try again'}, status=200)

    return JsonResponse({'error': 'Invalid request method'}, status=405)




@csrf_exempt
def all_vehicle_listing(request):
    """Get all vehicle listings"""
    if request.method == 'POST':
        data = json.loads(request.body)
        email = data[0].get('email')  # Access first item since you're sending array
        print(f"email: {email}")
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

class VehicleListingViewSet(ModelViewSet):
    """Get all vehicle listings"""
    queryset = VehicleListing.objects.all().order_by('-updated_at')
    serializer_class = VehicleListingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    filterset_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    ordering_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    ordering = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    
    def get_queryset(self):
        """Get all vehicle listings"""
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
        if vehicle_listing.status== "pending" or vehicle_listing.status== "failed":
            vehicle_listing.delete()
            return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
        else:
            response = perform_search_and_delete(search_query,credentials.session_cookie)
            if response[0]:
                vehicle_listing.delete()
                return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
            else:
                return JsonResponse({'error': response[1]}, status=200)

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

def create_facebook_listing(vehicle_listing):
    """Create Facebook listing"""
    try:
        credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
        print(credentials.email)
        if credentials and credentials.session_cookie != {}:
            listing_created, message = create_marketplace_listing(vehicle_listing, credentials.session_cookie)
            if listing_created:
                print(message)
                already_listed = FacebookListing.objects.filter(user=vehicle_listing.user, listing=vehicle_listing).first()
                if not already_listed:
                    FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="success")
                    vehicle_listing.status="completed"
                    vehicle_listing.save()
                    return True, "Listing created successfully"
                else:
                    return False, "Listing already exists"
            else:
                FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="failed", error_message=message)
                vehicle_listing.status="failed"
                vehicle_listing.save()
                return False, message
        elif credentials and credentials.session_cookie == {}:
                session_cookie =login_to_facebook(credentials.email, credentials.password)
                if session_cookie:
                    credentials.session_cookie = session_cookie
                    credentials.save()
                    time.sleep(random.uniform(3,5))
                    listing_created, message = create_marketplace_listing(vehicle_listing, session_cookie) 
                    if listing_created:
                        print(message)
                        already_listed = FacebookListing.objects.filter(user=vehicle_listing.user, listing=vehicle_listing).first()
                        if not already_listed:
                            FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="success")
                            vehicle_listing.status="completed"
                            vehicle_listing.save()
                            return True, "Listing created successfully"
                        else:
                            return False, "Listing already exists"
                    else:
                        FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="failed", error_message=message)
                        vehicle_listing.status="failed"
                        vehicle_listing.save()
                        return False, message
                else:
                    return False, "Login failed."
        else:
            return False, "No credentials found for the user"
             
    except Exception as e:
        vehicle_listing.status="failed"
        vehicle_listing.save()
        return False, "Failed to create listing"
    
def create_facebook_marketplace_listing_task(vehicle_listing):
    """Create Facebook marketplace listing"""
    try:
        credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
        if credentials and credentials.session_cookie:
            listing_created, message = create_marketplace_listing(vehicle_listing, credentials.session_cookie)
            if listing_created:
                print(message)
                already_listed = FacebookListing.objects.filter(user=vehicle_listing.user, listing=vehicle_listing).first()
                if not already_listed:
                    FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="success")
                    vehicle_listing.status="completed"
                    vehicle_listing.save()
            else:
                    FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="failed", error_message=message)
                    vehicle_listing.status="failed"
                    vehicle_listing.save()
        elif credentials and not credentials.session_cookie:
            session_cookie =login_to_facebook(credentials.email, credentials.password)
            if session_cookie:
                credentials.session_cookie = session_cookie
                credentials.save()
                listing_created, message = create_marketplace_listing(vehicle_listing, session_cookie) 
                if listing_created:
                    already_listed = FacebookListing.objects.filter(user=vehicle_listing.user, listing=vehicle_listing).first()
                    if not already_listed:
                        FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="success")
                        vehicle_listing.status="completed"
                        vehicle_listing.save()
                else:
                    FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="failed", error_message=message)
                    vehicle_listing.status="failed"
                    vehicle_listing.save()
            else:
                FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="failed", error_message="Login failed.")
                vehicle_listing.status="failed"
                vehicle_listing.save()
                print("Login failed.")
        else:
            FacebookListing.objects.create(user=vehicle_listing.user, listing=vehicle_listing, status="failed", error_message="No credentials found for the user")
            vehicle_listing.status="failed"
            vehicle_listing.save()
            print("No credentials found for the user")
             
    except Exception as e:

        vehicle_listing.status="failed"
        vehicle_listing.save()
        print(f"Error in create_facebook_marketplace_listing_task: {e}")

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
        if not credentials:
            return JsonResponse({'error': 'Facebook credentials not found'}, status=404)
        if credentials.session_cookie == {}:
            session_cookie =login_to_facebook(credentials.email, credentials.password)
            if session_cookie:
                credentials.session_cookie = session_cookie
                credentials.save()
            else:
                return JsonResponse({'error': 'Login failed'}, status=400)
        success, listings = get_facebook_profile_listings(profile_url, credentials.session_cookie)

        if success:
            facebook_profile_listing_instance = FacebookProfileListing.objects.create(url=profile_url,user=user,status="pending",profile_id=seller_id,total_listings=len(listings))
            # Create a new thread to process the listings
            thread = threading.Thread(target=facebook_profile_listings_thread, args=(listings, credentials,user,seller_id,facebook_profile_listing_instance))
            thread.start()
            return JsonResponse({'message': 'Profile Listings are being processed'}, status=200)
        else:
            return JsonResponse({'error': 'Failed to get listings'}, status=200)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def facebook_profile_listings_thread(listings, credentials,user,seller_id,facebook_profile_listing_instance):
    """Get listings using the function"""
    count=0
    for current_listing_key in listings:
        current_listing=listings[current_listing_key]
        already_listed = VehicleListing.objects.filter(user=user, list_id=current_listing["id"]).first()
        if already_listed:
            continue
        time.sleep(random.uniform(1,2))
        # Get listings using the function
        vehicleListing=extract_facebook_listing_details(current_listing, credentials.session_cookie)
        if vehicleListing:
            count+=1
            VehicleListing.objects.create(
                user=user,
                facebook_profile=facebook_profile_listing_instance,
                list_id=current_listing["id"],
                year=vehicleListing["year"],
                body_type="Other",
                fuel_type="Other",
                color="Other",
                variant="Other",
                make=vehicleListing["make"],
                mileage=current_listing["mileage"],
                model=vehicleListing["model"],
                price=vehicleListing.get("price"),
                transmission=None,
                description=vehicleListing.get("description"),
                images=vehicleListing["images"],
                url=current_listing["url"],
                location=vehicleListing.get("location"),
                status="pending",
                seller_profile_id=seller_id
            )
    facebook_profile_listing_instance.status="completed"
    facebook_profile_listing_instance.processed_listings=count
    facebook_profile_listing_instance.save()

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
        facebook_profile_vehicle_listings=VehicleListing.objects.filter(facebook_profile=facebook_profile_listing,status="completed").all()
        if facebook_profile_vehicle_listings:
            user = facebook_profile_listing.user
            credentials = FacebookUserCredentials.objects.filter(user=user).first()
            if credentials and credentials.session_cookie != {}:
                session_cookie = credentials.session_cookie
            for current_listing in facebook_profile_vehicle_listings:
                year_make_model_list.append(current_listing.year + " " + current_listing.make + " " + current_listing.model)
        facebook_profile_listing.delete()
        if year_make_model_list:
            thread = threading.Thread(target=delete_multiple_vehicle_listings, args=(year_make_model_list,session_cookie))
            thread.start()
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
        gumtree_profile_vehicle_listings=VehicleListing.objects.filter(gumtree_profile=gumtree_profile_listing,status="completed").all()
        if gumtree_profile_vehicle_listings:
            user = gumtree_profile_listing.user
            credentials = FacebookUserCredentials.objects.filter(user=user).first()
            if credentials and credentials.session_cookie != {}:
                session_cookie = credentials.session_cookie
            for current_listing in gumtree_profile_vehicle_listings:
                year_make_model_list.append(current_listing.year + " " + current_listing.make + " " + current_listing.model)
        gumtree_profile_listing.delete()
        if year_make_model_list:
            thread = threading.Thread(target=delete_multiple_vehicle_listings, args=(year_make_model_list,session_cookie))
            thread.start()
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
        for current_listing in year_make_model_list:
            search_query = current_listing
            perform_search_and_delete(search_query,session_cookie)
            time.sleep(random.uniform(2,3))
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


