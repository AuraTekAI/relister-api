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
from datetime import datetime
import re
import csv
from io import StringIO
from relister.settings import EMAIL_HOST_USER,MAX_RETRIES_ATTEMPTS, ADMIN_EMAIL, TECH_SUPPORT_EMAIL

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


def send_user_approval_email(user):
    """
    Send approval notification email to newly approved users.
    """
    logger.info(f"Sending approval email to {user.email}")
    
    try:
        # Check if user has a profile URL set up
        has_profile_url = bool(user.gumtree_dealarship_url or user.facebook_dealership_url)
        
        # Render email template
        html_content = render_to_string("listings/user_approval_notification.html", {
            "user_name": user.contact_person_name or user.email,
            "dealership_name": user.dealership_name or "N/A",
            "user_email": user.email,
            "has_profile_url": has_profile_url,
        })
        
        # Send email
        email = EmailMessage(
            subject="🎉 Your Relister Account Has Been Approved!",
            body=html_content,
            from_email=EMAIL_HOST_USER,
            to=[user.email]
        )
        email.content_subtype = "html"
        email.send()
        
        logger.info(f"Approval email sent successfully to {user.email}")
        return True
        
    except Exception as e:
        logger.error(f"Error sending approval email to {user.email}: {e}")
        return False



def create_or_update_relisting_entry(listing, user, relisting=None):
    now = timezone.now()
    if not relisting:
        listing.has_images = False
        listing.is_relist = True
        listing.retry_count = 0
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
        relisting.listing.retry_count = 0
        relisting.listing.is_relist = True
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
        relisting.status = "completed"
        relisting.last_relisting_status = True
        relisting.updated_at = now
        relisting.save()
        #create new relisting entry
        logger.info(f"Relisting failed for the user {user.email} and listing title {listing.year} {listing.make} {listing.model}")
        RelistingFacebooklisting.objects.create(
            user=user,
            listing=relisting.listing,
            relisting_date=now,
            last_relisting_status=False,
            status="failed"
        )

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
def should_delete_listing(user):
    """Check if user is eligible to delete a listing based on time."""
    if not user.last_delete_listing_time:
        return True
    return timezone.now() - user.last_delete_listing_time >= timedelta(minutes=5)

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


def _clean_log_file(file_path, cutoff_date):
    """Clean old entries from a single log file"""    
    lines_removed = 0
    new_lines = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                # Match log format: INFO 2025-02-24 05:05:45,952 facebook_listing
                match = re.match(r'^\w+\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}),\d+', line)
                if match:
                    log_date_str = match.group(1)
                    try:
                        log_date = datetime.strptime(log_date_str, '%Y-%m-%d %H:%M:%S')
                        log_date = timezone.make_aware(log_date)
                        
                        if log_date >= cutoff_date:
                            new_lines.append(line)
                        else:
                            lines_removed += 1
                    except ValueError:
                        new_lines.append(line)  # Keep malformed lines
                else:
                    new_lines.append(line)  # Keep non-log lines
        
        # Write back only newer entries
        if lines_removed > 0:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
                
    except Exception as e:
        logger.error(f"Error cleaning log file {file_path}: {e}")
    
    return lines_removed


def send_missing_listing_notification(listing_id, year, make, model, listed_on, price, user_email):
    """Send email notification when listing not found on Facebook"""
    logger.info(f"Sending missing listing notification for {year} {make} {model}")
    
    try:
        user = User.objects.get(email=user_email)
        
        # Prepare email data
        email_data = {
            'listing_id': listing_id,
            'year': year,
            'make': make,
            'model': model,
            'listed_on': listed_on,
            'price': price,
            'user_name': user.contact_person_name or user.email,
            'dealership_name': user.dealership_name or 'N/A'
        }
        
        # Render email template
        html_content = render_to_string('listings/missing_listing_notification.html', email_data)
        
        # Send to user
        user_email_obj = EmailMessage(
            subject=f"Action Required: Facebook Listing Not Found - {year} {make} {model}",
            body=html_content,
            from_email=EMAIL_HOST_USER,
            to=[user.email,TECH_SUPPORT_EMAIL]
        )
        user_email_obj.content_subtype = 'html'
        user_email_obj.send()
        
        logger.info(f"Missing listing notification sent to {user.email} and {ADMIN_EMAIL}")
        
    except Exception as e:
        logger.error(f"Error sending missing listing notification: {e}")
        raise


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
    
    # User statistics
    if data.get('user_statistics'):
        writer.writerow(['USER STATISTICS'])
        writer.writerow(['Email', 'Dealership', 'Contact Person', 'Daily Count', 'Total Listings', 'Total Relistings', 'Yesterday Relistings', 'Yesterday Failed', 'Ready for Relisting', 'Active', 'Failed', 'Pending', 'Sold'])
        for user in data['user_statistics']:
            writer.writerow([
                user['email'], user['dealership_name'], user['contact_person_name'],
                user['daily_listing_count'], user['total_listings'], user['total_relistings'],
                user['yesterday_relistings'], user['yesterday_failed_relistings'],
                user['ready_for_relisting'], user['active_listings'], user['failed_listings'],
                user['pending_listings'], user['sold_listings']
            ])
        writer.writerow([])
    
    return output.getvalue()