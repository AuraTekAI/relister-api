from relister.celery import CustomExceptionHandler
from celery import shared_task
from VehicleListing.facebook_listing import create_marketplace_listing
from VehicleListing.models import VehicleListing, FacebookListing
from .models import FacebookUserCredentials
from datetime import datetime, timedelta
from VehicleListing.facebook_listing import perform_search_and_delete
import time
import random
import logging

logger = logging.getLogger('facebook_listing_cronjob')

@shared_task(bind=True, base=CustomExceptionHandler,queue='scheduling_queue')
def create_pending_facebook_marketplace_listing_task(self):
    """Create pending facebook marketplace listings"""
    pending_listings = VehicleListing.objects.filter(status="pending").all()
    if pending_listings:
        for listing in pending_listings:
            try:
                logger.info(f"Creating pending facebook marketplace listing for the user {listing.user.email}")
                time.sleep(random.randint(2,5))
                credentials = FacebookUserCredentials.objects.filter(user=listing.user).first()
                if credentials and credentials.session_cookie != {}:
                    already_listed = FacebookListing.objects.filter(user=listing.user, listing=listing).first()
                    if already_listed:
                        logger.info(f"Listing already exists for the user {listing.user.email}")
                        continue
                    else:
                        listing_created, message = create_marketplace_listing(listing, credentials.session_cookie)
                        if listing_created:
                            logger.info(f"Listing created successfully for the user {listing.user.email}")
                            FacebookListing.objects.create(user=listing.user, listing=listing, status="success",error_message=message)
                            listing.status="completed"
                            listing.save()
                        else:
                            logger.info(f"Listing failed for the user {listing.user.email}")
                            FacebookListing.objects.create(user=listing.user, listing=listing, status="failed", error_message=message)
                            listing.status="failed"
                            listing.save()
                else:
                    logger.info(f"No credentials found for the user {listing.user.email}")
            except Exception as e:
                logger.info(f"Error creating pending facebook marketplace listing for the user {listing.user.email}")
                raise e
    else:
        logger.info("No pending listings found for facebook marketplace")


@shared_task(bind=True, base=CustomExceptionHandler,queue='scheduling_queue')
def relist_facebook_marketplace_listing_task(self):
    """Relist 7 days old facebook marketplace listings"""
    logger.info("Relisting 7 days old facebook marketplace listings")
    current_date = datetime.now().date()
    seven_days_ago = current_date - timedelta(days=7)
    pending_listings = VehicleListing.objects.filter(status="completed", updated_at__date__lte=seven_days_ago).all()
    if pending_listings:
        for listing in pending_listings:
            logger.info(f"Relisting 7 days old facebook marketplace listing for the user {listing.user.email}")
            time.sleep(random.randint(2,5))
            search_query = listing.year + " " + listing.make + " " + listing.model
            credentials = FacebookUserCredentials.objects.filter(user=listing.user).first()
            if credentials and credentials.session_cookie != {}:
                logger.info(f"Credentials found for the user {listing.user.email}")
                response = perform_search_and_delete(search_query,credentials.session_cookie)
                if response[0]:
                    logger.info(f"Relisting found and deleted for the user {listing.user.email}")
                    listing_created, message = create_marketplace_listing(listing,credentials.session_cookie)
                    if listing_created:
                        logger.info(f"Relisting created successfully for the user {listing.user.email}")
                        listing.updated_at = datetime.now()
                        listing.save()
                    else:
                        logger.info(f"Relisting failed for the user {listing.user.email}")
                        listing.status="failed"
                        listing.updated_at = datetime.now()
                        listing.save()
            else:
                logger.info(f"No facebook credentials found for the user {listing.user.email}")
                continue
    else:
        logger.info("No 7 days old listings found for facebook marketplace")



@shared_task(bind=True, base=CustomExceptionHandler,queue='scheduling_queue')
def create_failed_facebook_marketplace_listing_task(self):
    """Create failed facebook marketplace listings"""
    pending_listings = VehicleListing.objects.filter(status="failed").all()
    if pending_listings:
        for listing in pending_listings:
            try:
                logger.info(f"Creating failed facebook marketplace listing for the user {listing.user.email}")
                time.sleep(random.randint(2,5))
                credentials = FacebookUserCredentials.objects.filter(user=listing.user).first()
                if credentials and credentials.session_cookie != {}:
                    already_listed = FacebookListing.objects.filter(user=listing.user, listing=listing).first()
                    if already_listed:
                        logger.info(f"Listing already exists for the user {listing.user.email}")
                        continue
                    else:
                        listing_created, message = create_marketplace_listing(listing, credentials.session_cookie)
                        if listing_created:
                            logger.info(f"Created listing successfully for the user {listing.user.email}")
                            FacebookListing.objects.create(user=listing.user, listing=listing, status="success",error_message=message)
                            listing.status="completed"
                            listing.save()
                        else:
                            logger.info(f"Failed to create listing for the user {listing.user.email}")
                            FacebookListing.objects.create(user=listing.user, listing=listing, status="failed", error_message=message)
                            listing.status="failed"
                            listing.save()
                else:
                    logger.info(f"No facebook credentials found for the user {listing.user.email}")
            except Exception as e:
                logger.info(f"Error creating failed facebook marketplace listing for the user {listing.user.email}")
                raise e
    else:
        logger.info("No failed listings found for facebook marketplace")
