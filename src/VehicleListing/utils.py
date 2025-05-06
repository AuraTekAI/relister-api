import logging
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from relister.settings import EMAIL_HOST_USER
import time
import random
from django.utils import timezone
from .models import FacebookUserCredentials, RelistingFacebooklisting
from .tasks import create_marketplace_listing
from relister.settings import MAX_RETRIES_ATTEMPTS

logger = logging.getLogger('facebook_listing_cronjob')
def send_status_reminder_email(facebook_user):
    """
    Send Facebook login status reminder email to users who have not logged in to Facebook for a relister or session is expired
    """
    logger.info("Status reminder email task started.")
    
    # Create HTML invoice (use a template if desired)
    if facebook_user.status_reminder == False:
        html_content = render_to_string("listings/facebook_session_status_reminder.html", {
            "user_name": facebook_user.user.contact_person_name,
        })

        # Send email
        email = EmailMessage(
            subject=f"Reminder: Facebook login session",
            body=html_content,
            from_email=EMAIL_HOST_USER,
            to=[facebook_user.user.email]
        )
        email.content_subtype = "html"
        email.send()
        facebook_user.status_reminder = True
        facebook_user.save()
        logger.info(f"Status reminder email sent to {facebook_user.user.email}")
        time.sleep(random.randint(2,3))
    else:   
        logger.info(f"Status reminder email already sent to {facebook_user.user.email}")



def create_or_update_relisting_entry(listing, user, relisting=None):
    now = timezone.now()
    if not relisting:
        listing.is_relist = True
        listing.save()
    else:
        relisting.updated_at = now
        relisting.last_relisting_status = True
        relisting.save()
    RelistingFacebooklisting.objects.create(
        user=user,
        listing=listing,
        relisting_date=now,
        last_relisting_status=False,
        status="completed"
    )

def handle_failed_relisting(listing, user, relisting=None):
    now = timezone.now()
    if not relisting:
        listing.is_relist = True
        listing.save()
        logger.info(f"Relisting failed for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
        RelistingFacebooklisting.objects.create(
            user=user,
            listing=listing,
            relisting_date=now,
            last_relisting_status=False,
            status="failed"
        )
    else:
        relisting.status = "failed"
        relisting.updated_at = now
        relisting.save()

def mark_listing_sold(listing, relisting=None):
    now = timezone.now()
    listing.is_relist = True
    listing.status = "sold"
    listing.updated_at = now
    listing.save()
    logger.info(f"Listing sold for the user {listing.user.email} and listing title {listing.year} {listing.make} {listing.model}")
    if relisting:
        logger.info(f"Mark sold relisting as completed for the user {listing.user.email} and re-listing title {listing.year} {listing.make} {listing.model}")
        relisting.status = "completed"
        relisting.last_relisting_status = True
        relisting.save()

def handle_retry_or_disable_credentials(credentials, user):
    if credentials.retry_count < MAX_RETRIES_ATTEMPTS:
        credentials.retry_count += 1
        credentials.save()
        logger.info(f"Retrying relisting for user {user.email} (Attempt {credentials.retry_count})")
    else:
        credentials.status = False
        credentials.save()
        logger.warning(f"Max retry attempts reached. Credentials disabled for user {user.email}")

def retry_failed_relistings(seven_days_ago):
    failed_relistings = RelistingFacebooklisting.objects.filter(
        relisting_date__date__lte=seven_days_ago,
        listing__status="completed",
        last_relisting_status=False,
        status="failed"
    )
    if not failed_relistings:
        logger.info("No failed relistings found for the user {user.email}")
        return

    for relisting in failed_relistings:
        logger.info(f"Relisting failed for the user {relisting.user.email} and re-listing title {relisting.listing.year} {relisting.listing.make} {relisting.listing.model}")
        credentials = FacebookUserCredentials.objects.filter(user=relisting.user).first()
        if not credentials or credentials.session_cookie == {} or not credentials.status:
            if credentials:
                credentials.status = False
                credentials.save()
                send_status_reminder_email(credentials)
            logger.warning(f"No valid credentials for user {relisting.user.email}")
            continue
        time.sleep(random.randint(20,30))
        listing_created, message = create_marketplace_listing(relisting.listing, credentials.session_cookie)
        now = timezone.now()
        if listing_created:
            update_credentials_success(credentials)
            relisting.status = "completed"
            logger.info(f"Successfully relisting the failed relisting for the user {relisting.user.email} and re-listing title {relisting.listing.year} {relisting.listing.make} {relisting.listing.model}")
        else:
            relisting.status = "failed"
            logger.error(f"Failed to relisting the failed relisting for the user {relisting.user.email} and re-listing title {relisting.listing.year} {relisting.listing.make} {relisting.listing.model}")
        relisting.updated_at = now
        relisting.relisting_date = now
        relisting.save()


#Helping function to update the credentials status to true and retry count to 0
def update_credentials_success(credentials):
    credentials.status = True
    credentials.retry_count = 0
    credentials.save()