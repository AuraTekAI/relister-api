from relister.celery import CustomExceptionHandler
from celery import shared_task
from VehicleListing.facebook_listing import create_marketplace_listing,verify_facebook_listing_images_upload
from VehicleListing.models import VehicleListing, FacebookListing, GumtreeProfileListing, FacebookProfileListing, RelistingFacebooklisting, Invoice
from .models import FacebookUserCredentials
from datetime import datetime, timedelta
from django.utils import timezone
import pytz
from VehicleListing.facebook_listing import get_facebook_profile_listings, perform_search_and_delete
from VehicleListing.gumtree_scraper import get_gumtree_listings,extract_seller_id
from VehicleListing.views import facebook_profile_listings_thread,image_verification
from accounts.models import User
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from .utils import send_status_reminder_email,mark_listing_sold,retry_failed_relistings,handle_retry_or_disable_credentials,create_or_update_relisting_entry,handle_failed_relisting,update_credentials_success,should_create_listing,should_check_images_upload_status_time
from relister.settings import EMAIL_HOST_USER,MAX_RETRIES_ATTEMPTS
from openpyxl import Workbook
from django.conf import settings
import uuid
import time
import random
import logging
import threading
logger = logging.getLogger('facebook_listing_cronjob')

# Get Australian Perth timezone
perth_tz = pytz.timezone('Australia/Perth')

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

            if FacebookListing.objects.filter(user=user, listing=listing, status="success").exists():
                listing.status = "completed"
                listing.listed_on = timezone.now().astimezone(perth_tz)
                listing.save()
                logger.info(f"Already listed: {user.email} - {listing.year} {listing.make} {listing.model}")
                continue

            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))

            if not should_create_listing(user):
                logger.info(f"10-minute cooldown for user {user.email}")
                pending_listings.append(listing)  # Re-queue for later
                continue

            created, message = create_marketplace_listing(listing, credentials.session_cookie)
            now = timezone.now().astimezone(perth_tz)
    
            if created:
                update_credentials_success(credentials)
                FacebookListing.objects.create(user=user, listing=listing, status="success", error_message=message)
                listing.status = "completed"
                listing.listed_on = now
                listing.updated_at = now
                listing.save()
                user.last_facebook_listing_time = now
                user.save()
                logger.info(f"Created: {user.email} - {listing.year} {listing.make} {listing.model}")
                time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                image_verification(listing)
            else:
                FacebookListing.objects.create(user=user, listing=listing, status="failed", error_message=message)
                listing.status = "failed"
                listing.save()
                credentials.retry_count += 1
                if credentials.retry_count >= MAX_RETRIES_ATTEMPTS:
                    credentials.status = False
                credentials.save()
                logger.info(f"Failed: {user.email} - {listing.year} {listing.make} {listing.model}")

        except Exception as e:
            logger.exception(f"Error creating listing for {user.email}: {e}")
            continue

    logger.info("Completed all pending Facebook listings.")


@shared_task(bind=True, base=CustomExceptionHandler, queue='scheduling_queue')
def relist_facebook_marketplace_listing_task(self):
    """Relist 7-day-old Facebook Marketplace listings"""
    logger.info("Relisting 7 days old facebook marketplace listings")
    current_date = timezone.now().astimezone(perth_tz).date()
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
        retry_failed_relistings(seven_days_ago)
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
        search_query = f"{listing.year} {listing.make} {listing.model}"
        logger.info(f"Searching and deleting the listing {search_query}")
        time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
        response = perform_search_and_delete(search_query,relisting_price, relisting_date,credentials.session_cookie)

        if response[0] == 1:  # Deletion successful
            logger.info(f"Old listing deleted for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
            logger.info(f"Relist the listing for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
            listing_created, message = create_marketplace_listing(listing, credentials.session_cookie)

            if listing_created:
                update_credentials_success(credentials)
                logger.info(f"Relisting successful for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                create_or_update_relisting_entry(listing, user, relisting)
                time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                image_verification(listing)
            else:
                logger.error(f"Relisting failed for user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
                handle_failed_relisting(listing, user, relisting)
        elif response[0] == 2:  # Listing sold
            logger.info(f"Listing sold for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
            mark_listing_sold(listing, relisting)
        else:
            handle_retry_or_disable_credentials(credentials, user)
    logger.info(f"Now, Relisting failed listing")
    retry_failed_relistings(seven_days_ago)


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

            if FacebookListing.objects.filter(user=user, listing=listing, status="success").exists():
                listing.status = "completed"
                listing.listed_on = timezone.now().astimezone(perth_tz)
                listing.save()
                logger.info(f"Already listed: {user.email} - {listing.year} {listing.make} {listing.model}")
                continue

            time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))

            if not should_create_listing(user):
                logger.info(f"10-minute cooldown for user {user.email}")
                failed_listings.append(listing)  # Re-queue for later
                continue

            created, message = create_marketplace_listing(listing, credentials.session_cookie)
            now = timezone.now().astimezone(perth_tz)

            if created:
                update_credentials_success(credentials)
                FacebookListing.objects.create(user=user, listing=listing, status="success", error_message=message)
                listing.status = "completed"
                listing.listed_on = now
                listing.updated_at = now
                listing.save()
                user.last_facebook_listing_time = now
                user.save()
                logger.info(f"Created: {user.email} - {listing.year} {listing.make} {listing.model}")
                time.sleep(random.randint(settings.DELAY_START_TIME_BEFORE_ACCESS_BROWSER, settings.DELAY_END_TIME_BEFORE_ACCESS_BROWSER))
                image_verification(listing)
            else:
                FacebookListing.objects.create(user=user, listing=listing, status="failed", error_message=message)
                listing.status = "failed"
                listing.save()
                credentials.retry_count += 1
                if credentials.retry_count >= MAX_RETRIES_ATTEMPTS:
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
    facebook_profile_listings = FacebookProfileListing.objects.all()
    if facebook_profile_listings:
        for facebook_profile_listing_instance in facebook_profile_listings:
            time.sleep(random.randint(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
            credentials = FacebookUserCredentials.objects.filter(user=facebook_profile_listing_instance.user).first()
            if credentials and credentials.session_cookie != {} and credentials.status:
                success, listings = get_facebook_profile_listings(facebook_profile_listing_instance.url,credentials.session_cookie)
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
    cutoff_date = timezone.now().astimezone(perth_tz).replace(day=1)
    today_date = timezone.now().astimezone(perth_tz).strftime("%d/%m/%Y")

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
                logger.info(f"item.listed_on: {item.listed_on}")
                listing_date = item.listed_on
                price = item.price
            else:
                logger.info(f"item.listing.price: {item.listing.price}")
                logger.info(f"item.relisting_date: {item.relisting_date}")
                listing_date = item.relisting_date
                price = item.listing.price
            response = verify_facebook_listing_images_upload(search_query, price, listing_date, credentials.session_cookie)
            user.last_images_check_status_time = timezone.now().astimezone(perth_tz)
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
            elif response[0] == 3: #Image upload successful
                logger.info(f"{'Vehicle listing' if isinstance(item, VehicleListing) else 'Relisting'} is already marked as available")
                logger.info(f"info: {response[1]}")
                item.status = "sold"
                item.save()
                continue
            elif response[0] == 4: #Failed to check images upload status    
                logger.info("failed to check images upload status. need to retry...")
                logger.info(f"Error: {response[1]}")
                continue
            elif response[0] == 5: #facebook login failed
                logger.info(f"Facebook login failed for user {user.email}. please check your credentials")
                logger.info(f"Error: {response[1]}")
                handle_retry_or_disable_credentials(credentials, user)
                continue
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
