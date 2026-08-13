from django.core.management.base import BaseCommand

from VehicleListing.image_pipeline import sync_listing_images
from VehicleListing.models import VehicleListing


class Command(BaseCommand):
    help = (
        "One-off migration of the existing catalog onto the S3-hosted image pipeline: "
        "for every VehicleListing with raw scraped images but no VehicleListingImage "
        "slots yet, create pending slots and enqueue Celery tasks to download, convert "
        "and upload them. Safe to re-run — listings that already have slots are skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Only process the first N eligible listings (for a staged rollout).',
        )

    def handle(self, *args, **options):
        queryset = (
            VehicleListing.objects
            .exclude(images=None)
            .exclude(image_slots__isnull=False)
            .order_by('id')
            .distinct()
        )
        limit = options.get('limit')
        if limit:
            queryset = queryset[:limit]

        total = 0
        enqueued = 0
        for listing in queryset.iterator():
            total += 1
            if not listing.images:
                continue
            sync_listing_images(listing, listing.images)
            enqueued += 1
            if enqueued % 100 == 0:
                self.stdout.write(f"Enqueued {enqueued} listings so far...")

        self.stdout.write(self.style.SUCCESS(
            f"Backfill complete: {enqueued}/{total} listings enqueued for S3 image ingestion."
        ))
