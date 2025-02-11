from relister.celery import CustomExceptionHandler
from celery import shared_task
from VehicleListing.facebook_listing import create_marketplace_listing,login_to_facebook
from VehicleListing.models import VehicleListing, FacebookListing
from .models import FacebookUserCredentials
from datetime import datetime, timedelta
from VehicleListing.facebook_listing import perform_search_and_delete
import time
import random

@shared_task(bind=True, base=CustomExceptionHandler,queue='scheduling_queue')
def create_facebook_marketplace_listing_task(self):
    pending_listings = VehicleListing.objects.filter(status="pending").all()
    for listing in pending_listings:
        try:
            time.sleep(random.randint(2,5))
            credentials = FacebookUserCredentials.objects.filter(user=listing.user).first()
            if credentials and credentials.session_cookie:
                listing_created, message = create_marketplace_listing(listing, credentials.session_cookie)
                if listing_created:
                    print(message)
                    already_listed = FacebookListing.objects.filter(user=listing.user, listing=listing).first()
                    if not already_listed:
                        FacebookListing.objects.create(user=listing.user, listing=listing, status="success",error_message=message)
                        listing.status="completed"
                        listing.save()
                else:
                    FacebookListing.objects.create(user=listing.user, listing=listing, status="failed", error_message=message)
                    listing.status="failed"
                    listing.save()
            elif credentials and not credentials.session_cookie:
                session_cookie =login_to_facebook(credentials.email, credentials.password)
                if session_cookie:
                    credentials.session_cookie = session_cookie
                    credentials.save()
                    listing_created, message = create_marketplace_listing(listing, session_cookie) 
                    if listing_created:
                        print(message)
                        already_listed = FacebookListing.objects.filter(user=listing.user, listing=listing).first()
                        if not already_listed:
                            FacebookListing.objects.create(user=listing.user, listing=listing, status="success",error_message=message)
                            listing.status="completed"
                            listing.save()
                    else:
                        FacebookListing.objects.create(user=listing.user, listing=listing, status="failed", error_message=message)
                        listing.status="failed"
                        listing.save()
                else:
                    raise Exception("Login failed.")
            else:
                raise Exception("No credentials found for the user")
             
        except Exception as e:
            listing.status="failed"
            listing.save()
            raise e
        

@shared_task(bind=True, base=CustomExceptionHandler,queue='scheduling_queue')
def relist_facebook_marketplace_listing_task(self):
    current_date = datetime.now().date()
    seven_days_ago = current_date - timedelta(days=7)
    pending_listings = VehicleListing.objects.filter(status="completed", updated_at__date__lte=seven_days_ago).all()
    if pending_listings:
        for listing in pending_listings:
            time.sleep(random.randint(2,5))
            search_query = listing.year + " " + listing.make + " " + listing.model
            credentials = FacebookUserCredentials.objects.filter(user=listing.user).first()
            if credentials and credentials.session_cookie != {}:
                response = perform_search_and_delete(search_query,credentials.session_cookie)
                if response[0]:
                    listing_created, message = create_marketplace_listing(listing,credentials.session_cookie)
                    if listing_created:
                        listing.updated_at = datetime.now()
                        listing.save()
            elif credentials and credentials.session_cookie == {}:
                session_cookie =login_to_facebook(credentials.email, credentials.password)
                if session_cookie:
                    credentials.session_cookie = session_cookie
                    credentials.save()
                    response = perform_search_and_delete(search_query,session_cookie)
                    if response[0]:
                        listing_created, message = create_marketplace_listing(listing,session_cookie)
                        if listing_created:
                            listing.updated_at = datetime.now()
                            listing.save()
            else:
                continue
