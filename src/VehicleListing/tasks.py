from relister.celery import CustomExceptionHandler
from celery import shared_task
from VehicleListing.facebook_listing import create_marketplace_listing,verify_facebook_listing_images_upload
from VehicleListing.models import VehicleListing, FacebookListing, GumtreeProfileListing, FacebookProfileListing, RelistingFacebooklisting, Invoice
from .models import FacebookUserCredentials
from datetime import timedelta
from django.utils import timezone
from VehicleListing.facebook_listing import get_facebook_profile_listings, perform_search_and_delete
from VehicleListing.gumtree_scraper import get_gumtree_listings,extract_seller_id
from VehicleListing.views import facebook_profile_listings_thread,image_verification
from accounts.models import User
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from .utils import send_status_reminder_email,mark_listing_sold,handle_retry_or_disable_credentials,create_or_update_relisting_entry,handle_failed_relisting,update_credentials_success,should_create_listing,should_check_images_upload_status_time
from relister.settings import EMAIL_HOST_USER,MAX_RETRIES_ATTEMPTS, ADMIN_EMAIL
from openpyxl import Workbook
from django.conf import settings
import uuid
import time
import random
import logging
import threading
import csv
from io import StringIO
logger = logging.getLogger('facebook_listing_cronjob')

@shared_task(bind=True, base=CustomExceptionHandler, queue='scheduling_queue')
def create_pending_facebook_marketplace_listing_task(self):
    """Create pending Facebook Marketplace listings."""

    pending_listings = list(VehicleListing.objects.filter(status="pending").select_related("user"))
    logger.info(f"Found {len(pending_listings)} pending listings for Facebook Marketplace")

    if not pending_listings:
        logger.info("No pending listings found for Facebook Marketplace")
        return

    while pending_listings:
        listing = pending_listings.pop(0)
        user = listing.user

        try:
            logger.info(f"Processing pending listing: {user.email} - {listing.year} {listing.make} {listing.model}")
            time.sleep(random.randint(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))

            credentials = FacebookUserCredentials.objects.filter(user=user).first()

            if not credentials or not credentials.session_cookie or not credentials.status:
                if credentials:
                    credentials.status = False
                    credentials.save()
                    send_status_reminder_email(credentials)
                logger.info(f"Invalid credentials for {user.email}")
                continue

            # if FacebookListing.objects.filter(user=user, listing=listing, status="success").exists():
            #     listing.status = "completed"
            #     listing.listed_on = timezone.now()
            #     listing.save()
            #     logger.info(f"Already listed: {user.email} - {listing.year} {listing.make} {listing.model}")
            #     continue

            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))

            if not should_create_listing(user):
                logger.info(f"10-minute cooldown for user {user.email}")
                pending_listings.append(listing)  # Re-queue for later
                continue
            last_24_hours_time = timezone.now() - timedelta(hours=24)
            current_user=User.objects.filter(id=user.id).first()
            logger.info(f"Last facebook listing time: {current_user.last_facebook_listing_time} and last day time: {last_24_hours_time}")
            if current_user.last_facebook_listing_time and current_user.last_facebook_listing_time < last_24_hours_time:
                logger.info(f"Resetting daily listing count for user {user.email} and after 24 hours")
                current_user.daily_listing_count = 0
                current_user.save()
            if current_user.daily_listing_count >= 15:
                logger.info(f"Daily listing count limit reached for user {user.email}")
                continue
            created, message = create_marketplace_listing(listing, credentials.session_cookie)
    
            if created:
                update_credentials_success(credentials)
                FacebookListing.objects.create(user=user, listing=listing, status="success", error_message=message)
                listing.status = "completed"
                listing.listed_on = timezone.now()
                listing.updated_at = timezone.now()
                listing.save()
                current_user.last_facebook_listing_time = timezone.now()
                current_user.daily_listing_count += 1
                current_user.save()
                logger.info(f"Created: {user.email} - {listing.year} {listing.make} {listing.model}")
                time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                image_verification(None,listing)
            else:
                FacebookListing.objects.create(user=user, listing=listing, status="failed", error_message=message)
                listing.status = "failed"
                listing.save()
                if credentials.retry_count <= MAX_RETRIES_ATTEMPTS:
                    credentials.retry_count += 1
                    credentials.save()
                else:
                    credentials.status = False
                    credentials.save()
                
                logger.info(f"Failed: {user.email} - {listing.year} {listing.make} {listing.model}")

        except Exception as e:
            logger.exception(f"Error creating listing for {user.email}: {e}")
            continue

    logger.info("Completed all pending Facebook listings.")


def retry_failed_relistings():
    failed_relistings = list(RelistingFacebooklisting.objects.filter(
        listing__status="completed",
        last_relisting_status=False,
        status="failed"
    ))
    if not failed_relistings:
        logger.info("No failed relistings found for the user {user.email}")
        return

    while failed_relistings:
        relisting = failed_relistings.pop(0)
        logger.info(f"Relisting failed for the user {relisting.user.email} and re-listing title {relisting.listing.year} {relisting.listing.make} {relisting.listing.model}")
        credentials = FacebookUserCredentials.objects.filter(user=relisting.user).first()
        if not credentials or credentials.session_cookie == {} or not credentials.status:
            if credentials:
                credentials.status = False
                credentials.save()
                send_status_reminder_email(credentials)
            logger.warning(f"No valid credentials for user {relisting.user.email}")
            continue
        if relisting.user.daily_listing_count >= 15:
            logger.info(f"Daily listing count limit reached for user {relisting.user.email}")
            continue
        if not should_create_listing(relisting.user):
            logger.info(f"10-minute cooldown for user {relisting.user.email}")
            failed_relistings.append(relisting)  # Re-queue for later
            continue

        time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
        listing_created, message = create_marketplace_listing(relisting.listing, credentials.session_cookie)
        now = timezone.now()
        if listing_created:
            update_credentials_success(credentials)
            relisting.status = "completed"
            relisting.listing.has_images = False
            relisting.listing.save()
            relisting.updated_at = now
            relisting.last_relisting_status = False
            relisting.relisting_date = now
            relisting.save()
            relisting.user.daily_listing_count += 1 
            relisting.user.last_facebook_listing_time = now
            relisting.user.save()
            logger.info(f"Successfully relisting the failed relisting for the user {relisting.user.email} and re-listing title {relisting.listing.year} {relisting.listing.make} {relisting.listing.model}")
            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
            logger.info(f"Checking the images upload status for the relisting {relisting.listing.year} {relisting.listing.make} {relisting.listing.model}")
            image_verification(relisting,None)
        else:
            relisting.status = "failed"
            relisting.updated_at = now
            relisting.save()
            logger.error(f"Failed to relisting the failed relisting for the user {relisting.user.email} and re-listing title {relisting.listing.year} {relisting.listing.make} {relisting.listing.model}")
    logging.info("Completed retrying failed relistings process.")
        
        
        


@shared_task(bind=True, base=CustomExceptionHandler, queue='scheduling_queue')
def relist_facebook_marketplace_listing_task(self):
    """Relist 7-day-old Facebook Marketplace listings"""
    logger.info("Relisting 7 days old facebook marketplace listings")
    current_date = timezone.now().date()
    seven_days_ago = current_date - timedelta(days=7)

    vehicle_listings = VehicleListing.objects.filter(
        status="completed", listed_on__date__lte=seven_days_ago, is_relist=False
    )
    relistings = RelistingFacebooklisting.objects.filter(
        relisting_date__date__lte=seven_days_ago,
        listing__status="completed",listing__is_relist=True,
        last_relisting_status=False,
        status="completed"
    )
    listings_to_process = list(vehicle_listings) + list(relistings)
    logger.info(f"Found {len(listings_to_process)} 7 days old listings(completed) for Facebook Marketplace.")

    if not listings_to_process:
        logger.info("No 7 days old listings(completed) found for Facebook Marketplace.")
        logger.info(f"Now, Relisting failed re_listings")
        retry_failed_relistings()
        return

    for item in listings_to_process:
        if hasattr(item, 'listing'):  # RelistingFacebooklisting
            relisting, listing = item, item.listing
            user = relisting.user
            relisting_price = listing.price
            relisting_date=relisting.relisting_date
        else:  # VehicleListing
            relisting = None
            listing = item
            user = listing.user
            relisting_price = listing.price
            relisting_date=listing.listed_on

        logger.info(f"Processing relisting for user {user.email}")
        time.sleep(random.randint(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
        credentials = FacebookUserCredentials.objects.filter(user=user).first()
        if not credentials or not credentials.session_cookie or not credentials.status or credentials.session_cookie == {}:
            if credentials:
                credentials.status = False
                credentials.save()
                send_status_reminder_email(credentials)
            logger.warning(f"No valid Facebook credentials for user {user.email}")
            continue
        logger.info(f"Credentials found for user {user.email}")
        last_day_time = timezone.now() - timedelta(hours=24)
        current_user=User.objects.filter(id=user.id).first()
        logger.info(f"Last facebook listing time: {current_user.last_facebook_listing_time} and last day time: {last_day_time}")
        if current_user.last_facebook_listing_time and current_user.last_facebook_listing_time < last_day_time:
            logger.info(f"Resetting daily listing count for user {user.email} and after 24 hours")
            current_user.daily_listing_count = 0
            current_user.save()
        if current_user.daily_listing_count >= 15:
            logger.info(f"Daily listing count limit reached for user {user.email}")
            listing.status = "failed"
            listing.save()
            continue
        search_query = f"{listing.year} {listing.make} {listing.model}"
        logger.info(f"Searching and deleting the listing {search_query} and listing price {relisting_price} and relisting date {relisting_date} for the user {user.email}")
        time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
        response = perform_search_and_delete(search_query,relisting_price, timezone.localtime(relisting_date),credentials.session_cookie)

        if response[0] == 1:  # Deletion successful
            logger.info(f"Old listing deleted for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
            logger.info(f"Relist the listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
            listing_created, message = create_marketplace_listing(listing, credentials.session_cookie)

            if listing_created:
                update_credentials_success(credentials)
                logger.info(f"Relisting successful for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                current_user.daily_listing_count += 1
                current_user.save()
                relisting=create_or_update_relisting_entry(listing, user, relisting)
                logger.info(f"Relisting created for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} and relisting date {relisting.relisting_date}")
                logger.info(f"Relisting created for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                image_verification(relisting,None)
            else:
                logger.error(f"Relisting failed for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                handle_failed_relisting(listing, user, relisting)
        # elif response[0] == 2:  # Listing sold
        #     logger.info(f"Listing sold for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
        #     mark_listing_sold(listing, relisting)
        elif response[0] == 6:
            logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
            logger.info(f"response[1]: {response[1]}")
            logger.info(f"number of retries: {listing.retry_count}")
            if listing.retry_count <= MAX_RETRIES_ATTEMPTS:
                listing.retry_count += 1
                listing.save()
                logger.info(f"No matching listing found for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} and number of retries: {listing.retry_count}")
            else:
                listing.status = "sold"
                listing.save()
                logger.info(f"Listing sold for the user {user.email} and listing title {listing.year} {listing.make} {listing.model} and number of retries: {listing.retry_count}")
        elif response[0] == 0:
            handle_retry_or_disable_credentials(credentials, user)
        else:
            logger.error(f"Relisting failed for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
            logger.info(f"response[1]: {response[1]}")
    logger.info(f"Now, Relisting failed listing")
    retry_failed_relistings()


@shared_task(bind=True, base=CustomExceptionHandler, queue='scheduling_queue')
def create_failed_facebook_marketplace_listing_task(self):
    """Create failed Facebook Marketplace listings."""

    failed_listings = list(VehicleListing.objects.filter(status="failed").select_related("user"))
    logger.info(f"Found {len(failed_listings)} failed listings for Facebook Marketplace")

    if not failed_listings:
        logger.info("No failed listings found for Facebook Marketplace")
        return

    while failed_listings:
        listing = failed_listings.pop(0)
        user = listing.user

        try:
            logger.info(f"Processing failed listing: {user.email} - {listing.year} {listing.make} {listing.model}")
            time.sleep(random.randint(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))

            credentials = FacebookUserCredentials.objects.filter(user=user).first()

            if not credentials or not credentials.session_cookie or not credentials.status:
                if credentials:
                    credentials.status = False
                    credentials.save()
                    send_status_reminder_email(credentials)
                logger.info(f"Invalid credentials for {user.email}")
                continue

            # if FacebookListing.objects.filter(user=user, listing=listing, status="success").exists():
            #     listing.status = "completed"
            #     listing.listed_on = timezone.now()
            #     logger.info(f"Already listed: {user.email} - {listing.year} {listing.make} {listing.model}")
            #     continue

            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))

            if not should_create_listing(user):
                logger.info(f"10-minute cooldown for user {user.email}")
                failed_listings.append(listing)  # Re-queue for later
                continue
            last_24_hours_time = timezone.now() - timedelta(hours=24)
            current_user=User.objects.filter(id=user.id).first()
            logger.info(f"Last facebook listing time: {current_user.last_facebook_listing_time} and last day time: {last_24_hours_time}")
            if current_user.last_facebook_listing_time and current_user.last_facebook_listing_time < last_24_hours_time:
                logger.info(f"Resetting daily listing count for user {user.email} and after 24 hours")
                current_user.daily_listing_count = 0
                current_user.save()
            if current_user.daily_listing_count >= 15:
                logger.info(f"Daily listing count limit reached for user {user.email}")
                continue

            created, message = create_marketplace_listing(listing, credentials.session_cookie)

            if created:
                update_credentials_success(credentials)
                FacebookListing.objects.create(user=user, listing=listing, status="success", error_message=message)
                listing.status = "completed"
                listing.listed_on = timezone.now()
                listing.updated_at = timezone.now()
                listing.save()
                current_user.last_facebook_listing_time = timezone.now()
                current_user.daily_listing_count += 1
                current_user.save()
                logger.info(f"Created: {user.email} - {listing.year} {listing.make} {listing.model}")
                time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                image_verification(None,listing)
            else:
                FacebookListing.objects.create(user=user, listing=listing, status="failed", error_message=message)
                listing.status = "failed"
                listing.save()
                if credentials.retry_count <= MAX_RETRIES_ATTEMPTS:
                    credentials.retry_count += 1
                    credentials.save()
                else:
                    credentials.status = False
                    credentials.save()
                logger.info(f"Failed: {user.email} - {listing.year} {listing.make} {listing.model}")

        except Exception as e:
            logger.exception(f"Error creating listing for {user.email}: {e}")
            continue

    logger.info("Completed all pending Facebook listings.")

@shared_task(bind=True, base=CustomExceptionHandler,queue='relister_queue')
def check_gumtree_profile_relisting_task(self):
    """Check gumtree profile relisting"""
    logger.info("Checking gumtree profile relisting")
    gumtree_profile_listings = GumtreeProfileListing.objects.all()
    if gumtree_profile_listings:
        for gumtree_profile_listing in gumtree_profile_listings:
            time.sleep(random.randint(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
            logger.info(f"Checking gumtree profile relisting for the user {gumtree_profile_listing.user.email}")
            result, message = get_gumtree_listings(gumtree_profile_listing.url,gumtree_profile_listing.user)
            if result:
                logger.info(message)
            else:
                logger.error(message)

@shared_task(bind=True, base=CustomExceptionHandler,queue='relister_queue')
def check_facebook_profile_relisting_task(self):
    """Check facebook profile relisting"""
    logger.info("Checking facebook profile relisting")
    facebook_profile_listings = FacebookProfileListing.objects.all()
    if facebook_profile_listings:
        for facebook_profile_listing_instance in facebook_profile_listings:
            time.sleep(random.randint(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
            credentials = FacebookUserCredentials.objects.filter(user=facebook_profile_listing_instance.user).first()
            if credentials and credentials.session_cookie != {} and credentials.status:
                success, listings = get_facebook_profile_listings(facebook_profile_listing_instance.url,credentials.session_cookie)
                logger.info(f"Success: {success} and listings count: {len(listings)} for the user {facebook_profile_listing_instance.user.email} and profile id: {facebook_profile_listing_instance.profile_id}")
                if success:
                    update_credentials_success(credentials)
                    thread = threading.Thread(target=facebook_profile_listings_thread, args=(listings, credentials,facebook_profile_listing_instance.user,facebook_profile_listing_instance.profile_id,facebook_profile_listing_instance))
                    thread.start()
                else:
                    if credentials.retry_count < MAX_RETRIES_ATTEMPTS:
                        credentials.retry_count += 1
                        credentials.save()
                        logger.error("Failed to get listings")
                    else:
                        credentials.status = False
                        credentials.save()
                        logger.error("Failed to get listings")
            else:
                if credentials:
                    credentials.status = False
                    credentials.save()
                    send_status_reminder_email(credentials)
                logger.error("No facebook credentials found")

@shared_task(bind=True, base=CustomExceptionHandler, queue='relister_queue')
def profile_listings_for_approved_users(self, user_id):
    user_instance = User.objects.filter(id=user_id).first()

    if not user_instance:
        return "No user found for the given ID while processing approved users."
    logger.info(f" Starting profile listing processing for approved user (ID: {user_id})")

    try:
        if user_instance.gumtree_dealarship_url:
            gumtree_profile_url = user_instance.gumtree_dealarship_url
            seller_id = extract_seller_id(gumtree_profile_url)

            if not seller_id or not seller_id.isdigit():
                logger.warning("Invalid seller ID extracted from Gumtree profile URL.")
                return "Invalid seller ID"

            if GumtreeProfileListing.objects.filter(url=gumtree_profile_url, user=user_instance, profile_id=seller_id).exists():
                logger.info("Gumtree profile URL has already been processed for this user.")
                return "This Gumtree profile URL is already processed for the approved user"

            success, message = get_gumtree_listings(gumtree_profile_url, user_instance)

            if success:
                logger.info("Successfully retrieved and scheduled Gumtree listings.")
            else:
                logger.error(f"Gumtree listings fetch failed: {message}")

            return message

        elif user_instance.facebook_dealership_url:
            facebook_profile_url = user_instance.facebook_dealership_url
            seller_id = extract_seller_id(facebook_profile_url)

            if not seller_id or not seller_id.isdigit():
                logger.warning("Invalid seller ID from Facebook profile URL.")
                return "Invalid seller ID of Facebook profile URL for approved user"

            if FacebookProfileListing.objects.filter(url=facebook_profile_url, user=user_instance, profile_id=seller_id).exists():
                logger.info("Facebook profile URL already processed for this user.")
                return "This Facebook profile URL is already processed for the approved user"

            credentials = FacebookUserCredentials.objects.filter(user=user_instance).first()
            if not credentials or not credentials.session_cookie or credentials.status == False or credentials.session_cookie == {}:
                if credentials:
                    credentials.status = False
                    credentials.save()
                    send_status_reminder_email(credentials)
                logger.warning("Missing Facebook credentials for the user.")
                return "Facebook credentials not found for approved user"

            success, listings = get_facebook_profile_listings(facebook_profile_url, credentials.session_cookie)

            if success:
                listing_count = len(listings)
                facebook_profile_listing_instance = FacebookProfileListing.objects.create(
                    url=facebook_profile_url,
                    user=user_instance,
                    status="pending",
                    profile_id=seller_id,
                    total_listings=listing_count
                )

                threading.Thread(
                    target=facebook_profile_listings_thread,
                    args=(listings, credentials, user_instance, seller_id, facebook_profile_listing_instance)
                ).start()

                logger.info(f"Scheduled processing for {listing_count} Facebook listings.")
                return "Profile listings for approved user are being processed"
            else:
                logger.error("Failed to retrieve Facebook listings.")
                return "Failed to get listings for approved user"

        else:
            logger.warning("No dealership URL (Gumtree or Facebook) found for the approved user.")
            return "No dealership URL found for this approved user."

    except Exception as e:
        logger.exception("An unexpected error occurred while processing profile listings.")
        return f"An error occurred while processing: {str(e)}"
    
@shared_task(bind=True, base=CustomExceptionHandler, queue='relister_queue')
def generate_and_send_monthly_invoices(self):
    logger.info("Invoice generation task started.")
    cutoff_date = timezone.now().replace(day=1)
    today_date = timezone.now().strftime("%d/%m/%Y")

    approved_users = User.objects.filter(is_approved=True).all()
    logger.info(f"Found {approved_users.count()} approved users with subscription date <= {cutoff_date}.")
    try:
        for current_user in approved_users:
            logger.info(f"Processing user: {current_user.email} (ID: {current_user.id})")
            already_invoice_send=Invoice.objects.filter(user=current_user,created_at__gte=cutoff_date).first()
            if already_invoice_send:
                logger.info(f"Invoice already sent for the user {current_user.email}")
                continue
            facebook_listing_dict = {}
            total_due = 0
            username = current_user.contact_person_name
            invoice_rows = []

            # Listings and Relistings
            facebook_listings = VehicleListing.objects.filter(
                user=current_user, status__in=["completed","sold"],
                listed_on__gte=cutoff_date
            )
            relist_facebook_listing = RelistingFacebooklisting.objects.filter(
                user=current_user,
                relisting_date__gte=cutoff_date
            )
            logger.info(f"User {current_user.email} - New listings: {facebook_listings.count()}, Relistings: {relist_facebook_listing.count()}")

            # Process listings
            for listing in facebook_listings:
                listing_id = listing.id
                facebook_listing_dict[listing_id] = {
                    "list_id": listing.list_id,
                    "year": listing.year,
                    "make": listing.make,
                    "model": listing.model,
                    "rate": listing.rate,
                    "user": listing.user,
                    "listing_date": [listing.created_at],
                    "Relisting_time": 1
                }

            for relisting in relist_facebook_listing:
                listing = relisting.listing
                listing_id = listing.id

                if listing_id in facebook_listing_dict:
                    facebook_listing_dict[listing_id]["Relisting_time"] += 1
                    facebook_listing_dict[listing_id]["listing_date"].append(relisting.relisting_date)
                else:
                    facebook_listing_dict[listing_id] = {
                        "list_id": listing.list_id,
                        "year": listing.year,
                        "make": listing.make,
                        "model": listing.model,
                        "rate": listing.rate,
                        "user": listing.user,
                        "listing_date": [relisting.relisting_date],
                        "Relisting_time": 1
                    }

            # Generate invoice content
            for data in facebook_listing_dict.values():
                car_id = data["list_id"]
                car_name = f"{data['year']} {data['make']} {data['model']}"
                times_relisted = data["Relisting_time"]
                relist_dates = ", ".join([d.strftime("%d/%m/%Y %I:%M %p") for d in data["listing_date"]])
                total = data["rate"] * times_relisted
                total_due += total

                invoice_rows.append({
                    "car_id": car_id,
                    "car_name": car_name,
                    "relist_count": times_relisted,
                    "relist_dates": relist_dates,
                    "total": f"${total}"
                })

            # Generate invoice ID
            invoice_id = str(uuid.uuid4()).split("-")[0].upper()

            # Save to Invoice model
            Invoice.objects.create(
                invoice_id=invoice_id,
                user=current_user,
                details=str(invoice_rows),
                total_amount=total_due
            )
            logger.info(f"Invoice {invoice_id} saved for user {current_user.email} - Total Due: ${total_due}")

            # Create Excel file
            wb = Workbook()
            ws = wb.active
            ws.append(["Car ID", "Car Name", "Number of Times Relisted", "Relist Dates & Times", "Total"])
            for row in invoice_rows:
                ws.append([row["car_id"], row["car_name"], row["relist_count"], row["relist_dates"], row["total"]])
            ws.append(["Total", "", sum(row["relist_count"] for row in invoice_rows), "", f"${total_due}"])

            from io import BytesIO
            file_stream = BytesIO()
            wb.save(file_stream)
            file_stream.seek(0)
            logger.info(" - Excel invoice file created.")

            # Create HTML invoice (use a template if desired)
            html_invoice = render_to_string("listings/invoice_template.html", {
                "invoice_id": invoice_id,
                "invoice_date": today_date,
                "user_name": username,
                "total_due": f"${total_due}",
                "invoice_rows": invoice_rows,
            })
            logger.info(" - HTML invoice rendered.")

            # Send email
            email = EmailMessage(
                subject=f"Invoice #{invoice_id}",
                body=html_invoice,
                from_email=EMAIL_HOST_USER,
                to=[current_user.email]
            )
            email.content_subtype = "html"
            email.attach(f"Invoice_{invoice_id}.xlsx", file_stream.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            email.send()
            logger.info(f"Invoice #{invoice_id} sent to {current_user.email}")
            time.sleep(random.randint(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
    except Exception as e:
        logger.exception(f"Error processing invoice for user {current_user.email}: {str(e)}")
    logger.info("Monthly invoice generation task completed.")




@shared_task(bind=True, base=CustomExceptionHandler, queue='relister_queue')
def check_images_upload_status(self):
    """Check images upload status"""
    logger.info("Checking images upload status against listings")
    vehicle_listings = list(VehicleListing.objects.filter(status="completed", is_relist=False, has_images=False))
    relistings = list(RelistingFacebooklisting.objects.filter(
        listing__status="completed", listing__is_relist=True,
        last_relisting_status=False,
        status="completed", listing__has_images=False
    ))
    if vehicle_listings or relistings:
        combined_listings = vehicle_listings + relistings
        while combined_listings:
            item = combined_listings.pop(0)
            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
            logger.info(f"Checking images upload status for the {'vehicle listing' if isinstance(item, VehicleListing) else 'relisting'} {item.id}")

            # ⏱️ Cooldown logic
            if not should_check_images_upload_status_time(item.user):
                logger.info(f"10-minute cooldown for user {item.user.email}")
                combined_listings.append(item)  # Requeue to check later
                continue
            user = item.user
            logger.info(f"Checking images upload status for the {'vehicle listing' if isinstance(item, VehicleListing) else 'relisting'} {item.id}")
            credentials = FacebookUserCredentials.objects.filter(user=user).first()
            if not credentials or not credentials.session_cookie or not credentials.status or credentials.session_cookie == {}:
                if credentials:
                    credentials.status = False
                    credentials.save()
                    send_status_reminder_email(credentials)
                logger.warning(f"No valid Facebook credentials for user {user.email}")
                continue
            logger.info(f"Credentials found for user {user.email}")
            search_query = f"{item.year} {item.make} {item.model}" if isinstance(item, VehicleListing) else f"{item.listing.year} {item.listing.make} {item.listing.model}"
            logger.info(f"Searching and deleting the {'vehicle listing' if isinstance(item, VehicleListing) else 'relisting'} {search_query}")
            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
            if isinstance(item, VehicleListing):
                logger.info(f"item.price: {item.price}")
                logger.info(f"item.listed_on: {timezone.localtime(item.listed_on)}")
                listing_date = timezone.localtime(item.listed_on)
                price = item.price
            else:
                logger.info(f"item.listing.price: {item.listing.price}")
                logger.info(f"item.relisting_date: {timezone.localtime(item.relisting_date)}")
                listing_date = timezone.localtime(item.relisting_date)
                price = item.listing.price
            response = verify_facebook_listing_images_upload(search_query, price, listing_date, credentials.session_cookie)
            user.last_images_check_status_time = timezone.now()
            user.save()
            if response[0] == 1:  #Image upload successful
                logger.info(f"{'Vehicle listing' if isinstance(item, VehicleListing) else 'Relisting'} has images uploaded")
                item.has_images = True
                item.save()
            elif response[0] == 2: #Image upload failed
                logger.info(f"{'Vehicle listing' if isinstance(item, VehicleListing) else 'Relisting'} has not published yet")
                logger.info(f"Error: {response[1]}")
                logger.info("retring attempt to check images upload status")
                continue
            # elif response[0] == 3: #Image upload successful
            #     logger.info(f"{'Vehicle listing' if isinstance(item, VehicleListing) else 'Relisting'} is already marked as available")
            #     logger.info(f"info: {response[1]}")
            #     item.status = "sold"
            #     item.save()
            #     continue
            elif response[0] == 4: #Failed to check images upload status    
                logger.info("failed to check images upload status. need to retry...")
                logger.info(f"Error: {response[1]}")
                continue
            elif response[0] == 5: #facebook login failed
                logger.info(f"Facebook login failed for user {user.email}. please check your credentials")
                logger.info(f"Error: {response[1]}")
                handle_retry_or_disable_credentials(credentials, user)
                continue
            elif response[0] == 6:
                logger.info(f"No matching listing found for the user {user.email} and listing title {search_query}")
                logger.info(f"response[1]: {response[1]}")
                logger.info(f"number of retries: {item.retry_count}")
                if item.retry_count < MAX_RETRIES_ATTEMPTS:
                    item.retry_count += 1
                    item.save()
                    logger.info(f"No matching listing found for the user {user.email} and listing title {search_query} and number of retries: {item.retry_count}")
                else:
                    item.status = "sold"
                    item.save()
                    logger.info(f"Listing sold for the user {user.email} and listing title {search_query} and number of retries: {item.retry_count}")
            elif response[0] == 0: #No matching listing found
                logger.info(f"listing found for the {'vehicle listing' if isinstance(item, VehicleListing) else 'relisting'} {search_query} has no images uploaded, Successfully deleted and marked as failed")
                logger.info(f"info: {response[1]}")
                item.status = "failed"
                item.save()
                continue
            else: #Image upload failed
                logger.info(f"{'Vehicle listing' if isinstance(item, VehicleListing) else 'Relisting'} has no images uploaded")
                logger.info(f"info: {response[1]}")
                continue
    else:
        logger.info("No vehicle listings or relistings found for checking images upload status")


@shared_task(bind=True, base=CustomExceptionHandler, queue='relister_queue')
def reset_listing_count_for_users_task(self):
    """Reset listing count for users - Optimized version"""
    logger.info("Resetting listing count for users")
    
    # Get all approved users and reset their daily_listing_count in bulk
    users = User.objects.filter(is_approved=True).all()
    user_count = users.count()
    
    if user_count == 0:
        logger.info("No approved users found to reset listing count")
        return
    # Update all users in a single database query
    updated_count = users.update(daily_listing_count=0)
    
    logger.info(f"Listing count reset for {updated_count} approved users")
    logger.info("Listing count reset task completed")

@shared_task(bind=True, base=CustomExceptionHandler, queue='relister_queue')
def send_daily_activity_report(self):
    """Send daily listing activity report to admin email"""
    logger.info("Generating daily activity report")
    
    today = timezone.now().date()
    yesterday = today - timedelta(days=1)
    seven_days_ago = today - timedelta(days=7)
    
    try:
        # Get successful relisted items for the day
        relisted_items = RelistingFacebooklisting.objects.filter(
            relisting_date__date=yesterday,
            status='completed',
            user__is_approved=True
        ).select_related('listing', 'user')
        logger.info(f"Found {len(relisted_items)} relisted items for yesterday.")
        # Get failed relistings (yesterday)
        failed_relistings = RelistingFacebooklisting.objects.filter(
            relisting_date__date=yesterday,
            status='failed',
            user__is_approved=True
        ).select_related('listing', 'user')
        logger.info(f"Found {len(relisted_items)} relisted items and {len(failed_relistings)} failed relistings for yesterday.")
        
        # Get total successful relistings (all time)
        total_successful_relistings = RelistingFacebooklisting.objects.filter(
            status='completed',
            user__is_approved=True
        ).count()
        
        # Get total failed relistings (all time)
        total_failed_relistings = RelistingFacebooklisting.objects.filter(
            status='failed',
            user__is_approved=True
        ).count()
        logger.info(f"Total successful relistings: {total_successful_relistings}, Total failed relistings: {total_failed_relistings}")
        
        # Get active listings (completed yesterday)
        active_listings = VehicleListing.objects.filter(
            status='completed',
            listed_on__date=yesterday,
            user__is_approved=True
        ).select_related('user')
        logger.info(f"Found {len(active_listings)} active listings for yesterday.")
        
        # Get pending listings
        pending_listings = VehicleListing.objects.filter(
            status='pending',
            user__is_approved=True
        ).select_related('user')
        logger.info(f"Found {len(pending_listings)} pending listings.")
        
        # Get failed listings
        failed_listings = VehicleListing.objects.filter(
            status='failed',
            user__is_approved=True
        ).select_related('user')
        logger.info(f"Found {len(failed_listings)} failed listings.")
        
        # Get sold listings (updated yesterday)
        sold_listings = VehicleListing.objects.filter(
            status='sold',
            updated_at__date=yesterday,
            user__is_approved=True
        ).select_related('user')
        logger.info(f"Found {len(sold_listings)} sold listings for yesterday.")

        # Get items eligible for relisting (6 days old)
        vehicle_listings = VehicleListing.objects.filter(
        status="completed", listed_on__date__lte=seven_days_ago, is_relist=False
        ).select_related('user')
        logger.info(f"Found {len(vehicle_listings)} eligible items for relisting.")

        relistings = RelistingFacebooklisting.objects.filter(
        relisting_date__date__lte=seven_days_ago,
        listing__status="completed",listing__is_relist=True,
        last_relisting_status=False,
        status="completed"
        )
        logger.info(f"Found {len(relistings)} relistings eligible for relisting.")
        eligible_items_list = list(vehicle_listings) + [r.listing for r in relistings]
        # Get approved users
        approved_users = User.objects.filter(is_approved=True)
        
        # Prepare report data
        report_data = {
            'report_date': yesterday.strftime('%Y-%m-%d'),
            'generated_at': timezone.now().strftime('%Y-%m-%d %H:%M:%S'),
            'relisted_count': relisted_items.count(),
            'failed_relistings_count': failed_relistings.count(),
            'total_successful_relistings': total_successful_relistings,
            'total_failed_relistings': total_failed_relistings,
            'active_count': active_listings.count(),
            'pending_count': pending_listings.count(),
            'failed_count': failed_listings.count(),
            'sold_count': sold_listings.count(),
            'eligible_count': len(eligible_items_list),
            'approved_users_count': approved_users.count(),
            'relisted_items': [{
                'list_id': r.listing.list_id,
                'title': f"{r.listing.year} {r.listing.make} {r.listing.model}",
                'user': r.user.email,
                'price': r.listing.price,
                'timestamp': r.relisting_date.strftime('%H:%M:%S')
            } for r in relisted_items],
            'failed_relistings': [{
                'list_id': r.listing.list_id,
                'title': f"{r.listing.year} {r.listing.make} {r.listing.model}",
                'user': r.user.email,
                'price': r.listing.price,
                'error_reason': getattr(r, 'error_message', 'N/A'),
                'timestamp': r.relisting_date.strftime('%H:%M:%S')
            } for r in failed_relistings],
            'active_listings': [{
                'list_id': item.list_id,
                'title': f"{item.year} {item.make} {item.model}",
                'user': item.user.email,
                'price': item.price,
                'listed_at': item.listed_on.strftime('%H:%M:%S') if item.listed_on else 'N/A'
            } for item in active_listings],
            'pending_listings': [{
                'list_id': item.list_id,
                'title': f"{item.year} {item.make} {item.model}",
                'user': item.user.email,
                'price': item.price,
                'created_at': item.created_at.strftime('%Y-%m-%d %H:%M:%S')
            } for item in pending_listings],
            'failed_listings': [{
                'list_id': item.list_id,
                'title': f"{item.year} {item.make} {item.model}",
                'user': item.user.email,
                'price': item.price,
                'failed_at': item.updated_at.strftime('%Y-%m-%d %H:%M:%S')
            } for item in failed_listings],
            'sold_listings': [{
                'list_id': item.list_id,
                'title': f"{item.year} {item.make} {item.model}",
                'user': item.user.email,
                'price': item.price,
                'sold_at': item.updated_at.strftime('%H:%M:%S')
            } for item in sold_listings],
            'eligible_items': [{
                'list_id': item.list_id,
                'title': f"{item.year} {item.make} {item.model}",
                'user': item.user.email if hasattr(item, 'user') else 'N/A',
                'price': item.price,
                'last_listed': item.listed_on.strftime('%Y-%m-%d') if item.listed_on else 'N/A',
                'next_eligible': (item.listed_on + timedelta(days=7)).strftime('%Y-%m-%d') if item.listed_on else 'N/A'
            } for item in eligible_items_list],
            'approved_users': [{
                'email': user.email,
                'dealership_name': user.dealership_name or 'N/A',
                'contact_person_name': user.contact_person_name or 'N/A',
                'daily_listing_count': user.daily_listing_count
            } for user in approved_users]
        }
        
        # Generate CSV attachment
        csv_content = _generate_csv_report(report_data)
        
        # Render HTML email
        html_content = render_to_string('listings/daily_report_template.html', report_data)
        
        # Send email to admin
        email = EmailMessage(
            subject=f"Daily Activity Report - {yesterday.strftime('%Y-%m-%d')}",
            body=html_content,
            from_email=EMAIL_HOST_USER,
            to=[ADMIN_EMAIL]
        )
        email.content_subtype = 'html'
        email.attach(f'daily_report_{yesterday.strftime("%Y%m%d")}.csv', csv_content, 'text/csv')
        email.send()
        
        logger.info(f"Daily report sent to {ADMIN_EMAIL}")
        
    except Exception as e:
        logger.error(f"Error sending daily report to {ADMIN_EMAIL}: {e}")
    
    logger.info("Daily activity report task completed")

def _generate_csv_report(data):
    """Generate CSV content for the report"""
    output = StringIO()
    writer = csv.writer(output)
    
    # Summary
    writer.writerow(['DAILY ACTIVITY SUMMARY'])
    writer.writerow(['Date', data['report_date']])
    writer.writerow(['Yesterday Successful Relistings', data['relisted_count']])
    writer.writerow(['Yesterday Failed Relistings', data['failed_relistings_count']])
    writer.writerow(['Total Successful Relistings', data['total_successful_relistings']])
    writer.writerow(['Total Failed Relistings', data['total_failed_relistings']])
    writer.writerow(['Active Listings', data['active_count']])
    writer.writerow(['Pending Listings', data['pending_count']])
    writer.writerow(['Failed Listings', data['failed_count']])
    writer.writerow(['Sold Listings', data['sold_count']])
    writer.writerow(['Eligible for Relisting', data['eligible_count']])
    writer.writerow(['Approved Users', data['approved_users_count']])
    writer.writerow([])
    
    # Successful relistings
    if data['relisted_items']:
        writer.writerow(['SUCCESSFUL RELISTINGS'])
        writer.writerow(['ID', 'Title', 'User', 'Price', 'Timestamp'])
        for item in data['relisted_items']:
            writer.writerow([item['list_id'], item['title'], item['user'], item['price'], item['timestamp']])
        writer.writerow([])
    
    # Failed relistings
    if data['failed_relistings']:
        writer.writerow(['FAILED RELISTINGS'])
        writer.writerow(['ID', 'Title', 'User', 'Price', 'Error', 'Timestamp'])
        for item in data['failed_relistings']:
            writer.writerow([item['list_id'], item['title'], item['user'], item['price'], item['error_reason'], item['timestamp']])
        writer.writerow([])
    
    # Active listings
    if data['active_listings']:
        writer.writerow(['ACTIVE LISTINGS'])
        writer.writerow(['ID', 'Title', 'User', 'Price', 'Listed At'])
        for item in data['active_listings']:
            writer.writerow([item['list_id'], item['title'], item['user'], item['price'], item['listed_at']])
        writer.writerow([])
    
    # Pending listings
    if data['pending_listings']:
        writer.writerow(['PENDING LISTINGS'])
        writer.writerow(['ID', 'Title', 'User', 'Price', 'Created At'])
        for item in data['pending_listings']:
            writer.writerow([item['list_id'], item['title'], item['user'], item['price'], item['created_at']])
        writer.writerow([])
    
    # Failed listings
    if data['failed_listings']:
        writer.writerow(['FAILED LISTINGS'])
        writer.writerow(['ID', 'Title', 'User', 'Price', 'Failed At'])
        for item in data['failed_listings']:
            writer.writerow([item['list_id'], item['title'], item['user'], item['price'], item['failed_at']])
        writer.writerow([])
    
    # Sold listings
    if data['sold_listings']:
        writer.writerow(['SOLD LISTINGS'])
        writer.writerow(['ID', 'Title', 'User', 'Price', 'Sold At'])
        for item in data['sold_listings']:
            writer.writerow([item['list_id'], item['title'], item['user'], item['price'], item['sold_at']])
        writer.writerow([])
    
    # Eligible items
    if data['eligible_items']:
        writer.writerow(['ELIGIBLE FOR RELISTING'])
        writer.writerow(['ID', 'Title', 'User', 'Price', 'Last Listed', 'Next Eligible'])
        for item in data['eligible_items']:
            writer.writerow([item['list_id'], item['title'], item['user'], item['price'], item['last_listed'], item['next_eligible']])
        writer.writerow([])
    
    # Approved users
    if data['approved_users']:
        writer.writerow(['APPROVED USERS'])
        writer.writerow(['Email', 'Dealership Name', 'Contact Person', 'Daily Count'])
        for user in data['approved_users']:
            writer.writerow([user['email'], user['dealership_name'], user['contact_person_name'], user['daily_listing_count']])
    
    return output.getvalue()