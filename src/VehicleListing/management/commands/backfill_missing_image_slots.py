"""
Management command to backfill image slots for listings with images but no slots.

This handles listings that:
1. Were scraped when BYPASS_GUMTREE_IMAGE_HOSTING was True
2. Failed during sync_listing_images() due to exceptions
3. Have images field but no VehicleListingImage slots

Usage:
    python manage.py backfill_missing_image_slots [--dry-run] [--user-id=N]
"""

from django.core.management.base import BaseCommand
from django.db.models import Q, Count
from VehicleListing.models import VehicleListing, VehicleListingImage
from VehicleListing.image_pipeline import sync_listing_images
import logging

logger = logging.getLogger('vehicle_image_pipeline')


class Command(BaseCommand):
    help = 'Backfill image slots for listings with images but no VehicleListingImage slots'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            default=None,
            help='Only process listings for a specific user ID',
        )
        parser.add_argument(
            '--gumtree-only',
            action='store_true',
            help='Only process Gumtree listings (with gumtree_profile_id)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of listings to process',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        user_id = options['user_id']
        gumtree_only = options['gumtree_only']
        limit = options['limit']

        self.stdout.write("=" * 70)
        self.stdout.write("Backfill Missing Image Slots")
        self.stdout.write("=" * 70)

        # Build query for listings with images but no slots
        # A listing has images but no slots if:
        # - images field is not null AND not empty []
        # - No related VehicleListingImage rows
        query = VehicleListing.objects.filter(
            images__isnull=False  # Has images field
        ).exclude(
            images=[]  # Not empty
        ).annotate(
            slot_count=Count('image_slots')
        ).filter(
            slot_count=0  # No slots
        )

        if user_id:
            query = query.filter(user_id=user_id)

        if gumtree_only:
            query = query.filter(gumtree_profile_id__isnull=False)

        total = query.count()
        self.stdout.write(f"\nFound {total} listings with images but no slots")

        if limit:
            query = query[:limit]
            self.stdout.write(f"Processing {limit} listings (limited)")

        if dry_run:
            self.stdout.write("DRY RUN: Not making changes")
            self.stdout.write("\nListings to process:")
            for listing in query:
                self.stdout.write(
                    f"  ID: {listing.id} | "
                    f"{listing.year} {listing.make} {listing.model} | "
                    f"Images: {len(listing.images or [])} | "
                    f"User: {listing.user_id}"
                )
            self.stdout.write(f"\nWould process {len(list(query))} listings")
            return

        # Process listings
        processed = 0
        failed = 0
        skipped = 0

        self.stdout.write("\nProcessing listings...")
        for listing in query:
            try:
                images_count = len(listing.images or [])
                self.stdout.write(
                    f"[{processed + failed + 1}/{total}] "
                    f"ID={listing.id} | "
                    f"{listing.year} {listing.make} {listing.model} | "
                    f"Images={images_count}",
                    ending=""
                )

                # Validate images
                if not listing.images:
                    self.stdout.write(self.style.WARNING(" (SKIPPED: no images)"))
                    skipped += 1
                    continue

                # Re-sync images (create slots and enqueue tasks)
                sync_listing_images(listing, listing.images)

                # Verify slots were created
                slot_count = listing.image_slots.count()
                if slot_count == images_count:
                    self.stdout.write(
                        self.style.SUCCESS(f" ✓ Created {slot_count} slots")
                    )
                    processed += 1
                elif slot_count > 0:
                    self.stdout.write(
                        self.style.WARNING(
                            f" (PARTIAL: {slot_count}/{images_count} slots created)"
                        )
                    )
                    processed += 1
                else:
                    self.stdout.write(
                        self.style.ERROR(" ✗ No slots created (check logs)")
                    )
                    failed += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f" ✗ Error: {str(e)}")
                )
                logger.exception(
                    f"Error backfilling slots for listing id={listing.id}",
                    exc_info=e
                )
                failed += 1

        # Summary
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Processed: {processed}")
        self.stdout.write(f"Failed: {failed}")
        self.stdout.write(f"Skipped: {skipped}")
        self.stdout.write(f"Total: {processed + failed + skipped}")

        if failed == 0:
            self.stdout.write(self.style.SUCCESS("✓ All listings processed successfully"))
        else:
            self.stdout.write(
                self.style.WARNING(f"⚠️ {failed} listings failed (check logs for details)")
            )
