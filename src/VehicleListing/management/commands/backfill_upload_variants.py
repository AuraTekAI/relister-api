from django.conf import settings
from django.core.management.base import BaseCommand

from VehicleListing.image_pipeline import (
    _s3_client,
    build_upload_variant_bytes,
    s3_key_for,
    upload_variant,
)
from VehicleListing.models import HostedImage


class Command(BaseCommand):
    help = (
        "Backfill the FB-safe JPEG upload variant (HostedImage.upload_image) for "
        "already-hosted photos that predate it. The JPEG is generated from the "
        "stored 'large' WebP object we already own (no dependency on the volatile "
        "dealer source URL), uploaded alongside the WebP variants, and its key "
        "recorded. Idempotent: images that already have upload_image are skipped, "
        "so it's safe to re-run and to run in stages with --limit."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Only process the first N eligible images (staged rollout).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report how many images would be backfilled; change nothing.',
        )

    def handle(self, *args, **options):
        # Every hosted image now needs the JPEG — _resolve_extension_images serves
        # it to Facebook for Gumtree listings too, not just custom-domain ones.
        queryset = (
            HostedImage.objects
            .filter(status=HostedImage.STATUS_READY, upload_image='')
            .exclude(large_image='')
            .order_by('id')
        )
        limit = options.get('limit')
        if limit:
            queryset = queryset[:limit]

        if options.get('dry_run'):
            self.stdout.write(f"{queryset.count()} hosted image(s) would be backfilled.")
            return

        client = _s3_client()
        bucket = settings.AWS_VEHICLE_IMAGE_BUCKET
        done = 0
        failed = 0
        for hosted in queryset.iterator():
            try:
                obj = client.get_object(Bucket=bucket, Key=hosted.large_image)
                large_bytes = obj['Body'].read()
                jpeg_bytes, _w, _h = build_upload_variant_bytes(
                    large_bytes,
                    settings.VEHICLE_IMAGE_SIZES['large'],
                    settings.VEHICLE_IMAGE_UPLOAD_JPEG_QUALITY,
                )
                upload_key = s3_key_for(hosted.content_hash, 'upload', ext='jpg')
                upload_variant(upload_key, jpeg_bytes, content_type='image/jpeg')
                hosted.upload_image = upload_key
                hosted.save(update_fields=['upload_image', 'updated_at'])
                done += 1
                if done % 100 == 0:
                    self.stdout.write(f"Backfilled {done} images so far...")
            except Exception as exc:  # keep going — one bad object shouldn't abort the run
                failed += 1
                self.stderr.write(
                    f"Failed for HostedImage {hosted.pk} ({hosted.content_hash}): {exc}"
                )

        self.stdout.write(self.style.SUCCESS(
            f"Upload-variant backfill complete: {done} succeeded, {failed} failed."
        ))
