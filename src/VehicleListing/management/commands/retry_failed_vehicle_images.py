from django.core.management.base import BaseCommand

from VehicleListing.models import VehicleListingImage
from VehicleListing.tasks import process_vehicle_listing_image_task


class Command(BaseCommand):
    help = (
        "Requeue VehicleListingImage rows stuck FAILED from the Gumtree image-CDN "
        "403 (images.gumtree.com.au blocking ZenRows' default datacenter-IP proxy — "
        "see image_pipeline.download_image_bytes' mode=auto fix). The listings "
        "themselves published fine to Facebook (the Chrome extension fetches the "
        "same URL directly from the dealer's browser, not through our proxy), so "
        "this only repairs our own S3-hosted copy used by the storefront; it does "
        "not touch Facebook or re-publish anything. Safe to re-run — a slot already "
        "READY by the time its task runs is a no-op (see the task's own guard)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Only requeue the first N matching rows (for a staged rollout).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report how many rows would be requeued without touching anything.',
        )

    def handle(self, *args, **options):
        queryset = (
            VehicleListingImage.objects
            .filter(status=VehicleListingImage.STATUS_FAILED)
            .filter(source_url__icontains='images.gumtree.com.au')
            .order_by('id')
        )

        limit = options.get('limit')
        if limit:
            queryset = queryset[:limit]

        total = queryset.count()
        if options.get('dry_run'):
            self.stdout.write(self.style.WARNING(
                f"DRY RUN — would requeue {total} FAILED Gumtree image slot(s)."
            ))
            return

        requeued = 0
        for slot_id in queryset.values_list('id', flat=True).iterator():
            # Reset bookkeeping so this reads as a fresh attempt (a fresh Celery
            # retry counter, a clean error_message) rather than picking up where
            # the old, permanently-blocked attempt left off.
            VehicleListingImage.objects.filter(pk=slot_id).update(
                status=VehicleListingImage.STATUS_PENDING,
                retry_count=0,
                error_message=None,
            )
            process_vehicle_listing_image_task.delay(slot_id)
            requeued += 1
            if requeued % 100 == 0:
                self.stdout.write(f"Requeued {requeued}/{total} so far...")

        self.stdout.write(self.style.SUCCESS(
            f"Requeued {requeued}/{total} FAILED Gumtree image slot(s) for reprocessing."
        ))
