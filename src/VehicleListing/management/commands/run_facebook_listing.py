from django.core.management.base import BaseCommand
from VehicleListing.tasks import create_facebook_marketplace_listing

class Command(BaseCommand):
    help = 'Run the create_facebook_marketplace_listing task'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting the Facebook Marketplace listing creation process...")
        try:
            create_facebook_marketplace_listing()
            self.stdout.write(self.style.SUCCESS("Successfully ran the Facebook Marketplace listing creation process."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"An error occurred: {e}")) 