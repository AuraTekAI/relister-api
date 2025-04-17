from relister.celery import CustomExceptionHandler
from celery import shared_task
from VehicleListing.facebook_listing import create_marketplace_listing
from VehicleListing.models import VehicleListing, FacebookListing, GumtreeProfileListing, FacebookProfileListing, RelistingFacebooklisting, Invoice
from .models import FacebookUserCredentials
from datetime import datetime, timedelta
from django.utils import timezone
from VehicleListing.facebook_listing import get_facebook_profile_listings, Renew_listing
from VehicleListing.gumtree_scraper import get_gumtree_listings,extract_seller_id
from VehicleListing.views import facebook_profile_listings_thread
from accounts.models import User
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from relister.settings import EMAIL_HOST_USER
from openpyxl import Workbook
import uuid
import time
import random
import logging
import threading
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
                    already_listed = FacebookListing.objects.filter(user=listing.user, listing=listing,status="success").first()
                    if already_listed:
                        listing.status="completed"
                        listing.save()
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
    current_datetime=datetime.now()
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
                response = Renew_listing(search_query,credentials.session_cookie)
                if response[0]:
                    logger.info(f"Relisting found and Relist/Renew for the user {listing.user.email}")
                    listing.updated_at = datetime.now()
                    if not isinstance(listing.renew_date, list):
                        listing.renew_date = []
                    listing.renew_date.append(current_datetime.isoformat())
                    listing.save()
                    RelistingFacebooklisting.objects.create(user=listing.user, listing=listing, relisting_date=datetime.now())
                else:
                    logger.info(f"{response[1]}")
                    logger.info(f"Failed to renew the listing, Retry attempt after 24 hours")
            else:
                logger.info(f"No facebook credentials found for the user {listing.user.email}")
                continue
    else:
        logger.info("No 7 days old listings found for facebook marketplace")



@shared_task(bind=True, base=CustomExceptionHandler,queue='scheduling_queue')
def create_failed_facebook_marketplace_listing_task(self):
    """Create failed facebook marketplace listings"""
    failed_listings = VehicleListing.objects.filter(status="failed").all()
    if failed_listings:
        for listing in failed_listings:
            try:
                logger.info(f"Creating failed facebook marketplace listing for the user {listing.user.email}")
                time.sleep(random.randint(2,5))
                credentials = FacebookUserCredentials.objects.filter(user=listing.user).first()
                if credentials and credentials.session_cookie != {}:
                    already_listed = FacebookListing.objects.filter(user=listing.user, listing=listing, status="success").first()
                    if already_listed:
                        listing.status="completed"
                        listing.save()
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

@shared_task(bind=True, base=CustomExceptionHandler,queue='relister_queue')
def check_gumtree_profile_relisting_task(self):
    """Check gumtree profile relisting"""
    logger.info("Checking gumtree profile relisting")
    gumtree_profile_listings = GumtreeProfileListing.objects.all()
    if gumtree_profile_listings:
        for gumtree_profile_listing in gumtree_profile_listings:
            time.sleep(random.randint(1,3))
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
            time.sleep(random.randint(1,3))
            credentials = FacebookUserCredentials.objects.filter(user=facebook_profile_listing_instance.user).first()
            if credentials:
                success, listings = get_facebook_profile_listings(facebook_profile_listing_instance.url,credentials.session_cookie)
                if success:
                    thread = threading.Thread(target=facebook_profile_listings_thread, args=(listings, credentials,facebook_profile_listing_instance.user,facebook_profile_listing_instance.profile_id,facebook_profile_listing_instance))
                    thread.start()
                else:
                    logger.error("Failed to get listings")
            else:
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

            credentials = FacebookUserCredentials.objects.filter(user=user_instance).first()
            if not credentials or not credentials.session_cookie:
                logger.warning("Missing or empty Facebook credentials for the user.")
                return "No Facebook credentials found for the user. Please provide the credentials."

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
            if not credentials or not credentials.session_cookie:
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
            rate = current_user.rate
            username = current_user.contact_person_name
            invoice_rows = []

            # Listings and Relistings
            facebook_listings = VehicleListing.objects.filter(
                user=current_user, status="completed",
                created_at__gte=cutoff_date,
                renew_date__isnull=True
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
                total = rate * times_relisted
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
            time.sleep(random.randint(2,3))
    except Exception as e:
            logger.exception(f"Error processing invoice for user {current_user.email}: {str(e)}")
    logger.info("Monthly invoice generation task completed.")