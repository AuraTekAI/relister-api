from .gumtree_scraper import get_listings
from .url_importer import ImportFromUrl
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework import filters
from .serializers import VehicleListingSerializer, ListingUrlSerializer, FacebookUserCredentialsSerializer
from accounts.models import User
from .models import VehicleListing, ListingUrl, FacebookUserCredentials, FacebookListing
import json
from .facebook_listing import create_marketplace_listing,login_to_facebook, perform_search_and_delete
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import threading
# @csrf_exempt
# def import_url_from_gumtree(request):
#     if request.method == 'POST':
        
#         data = json.loads(request.body)
#         email = data[0].get('email')
#         url = data[0].get('url')
#         if not email:
#             return JsonResponse({'error': 'Email is required'}, status=200)
        
#         user = User.objects.filter(email=email).first()
#         if not user:
#             return JsonResponse({'error': 'User not found'}, status=200)
        
#         import_url = ImportFromUrl(url)
#         is_valid, error_message = import_url.validate()
#         if not is_valid:
#             return JsonResponse({'error': error_message}, status=200)
#         list_id = url.split('/')[-1]
#         if not list_id.isdigit():
#             return JsonResponse({'error': 'Invalid URL'}, status=200)
#         if ListingUrl.objects.filter(url=url).exists():
#             return JsonResponse({'error': 'URL already exists'}, status=200)
#         # Extract data from URL
#         vehicle_listing = get_listings(url,user)
#         listing_url = ListingUrl.objects.create(url=url, user=user , status='Completed')

#         vls = VehicleListingSerializer(vehicle_listing)
#         # vls.save()

#         if vehicle_listing:
#             print(f"vehicle_listing: {vehicle_listing}")
#             response = create_facebook_listing(vehicle_listing)
#             print(f"response: {response}")

#             if response:
#                 # Prepare user related listing data
#                 user_data = {
#                     'url': url,
#                     'message': response[1]
#                 }
#                 return JsonResponse(user_data, status=200)
#             else:
#                 return JsonResponse({'error': response[1]}, status=200)
#         else:
#             return JsonResponse({'error': 'Failed to extract data from URL'}, status=400)

#     return JsonResponse({'error': 'Invalid request method'}, status=405)





@csrf_exempt
def import_url_from_gumtree(request):
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
        list_id = url.split('/')[-1]
        if not list_id.isdigit():
            return JsonResponse({'error': 'Invalid URL'}, status=200)
        if ListingUrl.objects.filter(url=url).exists():
            return JsonResponse({'error': 'URL already exists'}, status=200)
        # Extract data from URL
        vehicle_listing = get_listings(url,user)
        listing_url = ListingUrl.objects.create(url=url, user=user , status='Completed')
        print(f"listing_url: {listing_url}")
        vls = VehicleListingSerializer(vehicle_listing)

        if vehicle_listing:
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
            return JsonResponse({'error': 'Failed to extract data from URL'}, status=200)

    return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def all_vehicle_listing(request):
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
    if request.method == 'POST':
        email = request.POST.get('email')
        user = User.objects.get(email=email).first()
        if not user:
            return JsonResponse({'error': 'User not found'}, status=200)
        all_urls = ListingUrl.objects.filter(user=user)
        return JsonResponse(all_urls, status=200)
    return JsonResponse({'error': 'Invalid request method'}, status=200)


class ListingUrlViewSet(ModelViewSet):
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
    queryset = VehicleListing.objects.all().order_by('-updated_at')
    serializer_class = VehicleListingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    filterset_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    ordering_fields = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    ordering = ['make', 'model', 'location', 'price', 'mileage', 'year', 'variant', 'fuel_type', 'description', 'images']
    
    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return VehicleListing.objects.all().order_by('-updated_at')
        else:
            return VehicleListing.objects.filter(user=user).order_by('-updated_at')

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        print(f"instance: {instance}")
        # Proceed with deletion
        vehicle_listing=VehicleListing.objects.filter(id=instance.id).first()
        search_query = vehicle_listing.year + " " + vehicle_listing.make + " " + vehicle_listing.model
        credentials = FacebookUserCredentials.objects.filter(user=vehicle_listing.user).first()
        if vehicle_listing.status== "pending" or vehicle_listing.status== "failed":
            vehicle_listing.delete()
            # url = vehicle_listing.url
            # import_url = ListingUrl.objects.filter(url=url).first()
            # import_url.delete()
            return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
        else:
            response = perform_search_and_delete(search_query,credentials.session_cookie)
            if response[0]:
                vehicle_listing.delete()
                # url = vehicle_listing.url
                # import_url = ListingUrl.objects.filter(url=url).first()
                # import_url.delete()
                return JsonResponse({'message': 'Listing deleted successfully'}, status=200)
            else:
                return JsonResponse({'error': response[1]}, status=200)

        

class FacebookUserCredentialsViewSet(ModelViewSet):
    queryset = FacebookUserCredentials.objects.all()
    serializer_class = FacebookUserCredentialsSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['email']
    filterset_fields = ['email']
    ordering_fields = ['email']
    ordering = ['email']
    

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return FacebookUserCredentials.objects.all()
        else:
            return FacebookUserCredentials.objects.filter(user=user)





def create_facebook_listing(vehicle_listing):
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
    try:
        print(vehicle_listing.user)
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