from django.core.management.base import BaseCommand
from VehicleListing.models import FacebookUserCredentials
from accounts.models import User
import asyncio
from playwright.sync_api import sync_playwright
import time

class Command(BaseCommand):
    help = 'Test Facebook Marketplace listings using user credentials (Playwright)'

    def handle(self, *args, **options):
        email = 'sami@gmail.com'
        try:
            user=User.objects.filter(email=email).first()
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User with email {email} does not exist'))
            return  # Exit the command if the user does not exist

        try:
            fb_cred = FacebookUserCredentials.objects.filter(user=user).first()
            if not fb_cred:
                self.stdout.write(self.style.ERROR(f'No credentials found for {email}'))
                return
        except FacebookUserCredentials.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'No credentials found for {email}'))
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False,args=["--start-maximized"])
            context = browser.new_context(storage_state=fb_cred.session_cookie,viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            # Navigate to the vehicle listing page      
            try:
                page.goto("https://www.facebook.com/marketplace/create/vehicle", timeout=60000)

                self.stdout.write(self.style.SUCCESS(f'Successfully opened Facebook Marketplace for {email}'))
                # Wait for the page to load completely
                time.sleep(10000)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'An error occurred: {str(e)}'))
                browser.close()
                return
            self.stdout.write(self.style.SUCCESS(f'Successfully navigated to Facebook Marketplace for {email}'))