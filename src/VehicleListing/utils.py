import logging
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from relister.settings import EMAIL_HOST_USER
import time
import random
from django.utils import timezone
from datetime import timedelta
from .models import  RelistingFacebooklisting
from relister.settings import MAX_RETRIES_ATTEMPTS
from accounts.models import User
from django.conf import settings

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
        time.sleep(random.randint(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))
    else:   
        logger.info(f"Status reminder email already sent to {facebook_user.user.email}")



def create_or_update_relisting_entry(listing, user, relisting=None):
    now = timezone.now()
    if not relisting:
        listing.has_images = False
        listing.is_relist = True
        listing.save()
        relisting=RelistingFacebooklisting.objects.create(
        user=user,
        listing=listing,
        relisting_date=now,
        last_relisting_status=False,
        status="completed"
    )
        relisting.save()
        logger.info(f"Relisting created for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
    else:
        relisting.listing.has_images = False
        relisting.listing.save()
        relisting.updated_at = now
        relisting.last_relisting_status = True
        relisting.save()
        relisting=RelistingFacebooklisting.objects.create(
        user=user,
        listing=relisting.listing,
        relisting_date=now,
        last_relisting_status=False,
        status="completed"
    )
        relisting.save()
        logger.info(f"Relisting updated for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
    
    return relisting

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

#Helping function to update the credentials status to true and retry count to 0
def update_credentials_success(credentials):
    credentials.status = True
    credentials.retry_count = 0
    credentials.save()

def should_create_listing(user):
    """Check if user is eligible to create a new listing based on time."""
    user=User.objects.filter(id=user.id).first()
    if not user.last_facebook_listing_time:
        return True
    return timezone.now() - user.last_facebook_listing_time >= timedelta(minutes=10)

def should_check_images_upload_status_time(user):
    """Check if user is eligible to check images upload status time based on time."""
    user=User.objects.filter(id=user.id).first()
    if not user.last_images_check_status_time:
        return True
    return timezone.now() - user.last_images_check_status_time >= timedelta(minutes=10)


def get_full_state_name(input_state):
    # Normalize input
    state_input = input_state.strip().lower()

    # Dictionary mapping all possible forms to their full names
    state_mapping = {
        'nsw': 'New South Wales',
        'new south wales': 'New South Wales',
        'n.s.w.': 'New South Wales',

        'vic': 'Victoria',
        'v': 'Victoria',
        'victoria': 'Victoria',

        'qld': 'Queensland',
        'q': 'Queensland',
        'queensland': 'Queensland',

        'wa': 'Western Australia',
        'w.a.': 'Western Australia',
        'w': 'Western Australia',
        'western australia': 'Western Australia',

        'sa': 'South Australia',
        's.a.': 'South Australia',
        's': 'South Australia',
        'south australia': 'South Australia',

        'tas': 'Tasmania',
        't': 'Tasmania',
        'tasmania': 'Tasmania',
    }

    return state_mapping.get(state_input, input_state)