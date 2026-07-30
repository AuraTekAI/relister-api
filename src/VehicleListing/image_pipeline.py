"""
Vehicle photo ingestion pipeline: download a scraped image once, convert it to
three WebP sizes, upload to our own S3 bucket, and record it so the storefront
API can serve it instead of the original Gumtree/dealer URL.

Call graph:
  gumtree_scraper.py / custom_domain_scraper.py
      -> sync_listing_images(listing, urls)      [diff against existing slots, enqueue new ones]
          -> tasks.process_vehicle_listing_image_task (Celery, per slot)
              -> download_image_bytes -> content_hash_for -> get_or_create_ready_hosted_image
                  -> build_variant_bytes -> upload_variant (S3)

Deletion is the mirror image: VehicleListing.image_slots cascade-delete with
their listing, VehicleListing/signals.py notices each slot's post_delete and
enqueues tasks.gc_hosted_image_task, which removes the S3 objects once no
listing references that HostedImage any more.
"""
import hashlib
import io
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import IntegrityError, transaction
from PIL import Image, ImageOps
from zenrows import ZenRowsClient

from .models import HostedImage, VehicleListingImage

logger = logging.getLogger('vehicle_image_pipeline')

# Gumtree/dealer CDNs occasionally serve HTML error pages with a 200 status;
# reject anything that isn't a plain raster image rather than store garbage.
_ALLOWED_CONTENT_TYPES = {'image/jpeg', 'image/pjpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp'}


def download_image_bytes(url, timeout):
    # Gumtree's image CDN (images.gumtree.com.au, a Cloudinary-backed host
    # fronted by the Peakhour bot-mitigation edge) 403s ANY datacenter-IP
    # request — confirmed by hitting the exact production-failing URL directly
    # from a plain, unproxied request: `peakhour-error: blocked` came back
    # regardless of User-Agent/Referer/Accept headers. ZenRows' *default*
    # proxy tier is itself datacenter IPs, so routing through plain
    # `client.get(url)` (no extra params) hits the identical block — that's
    # why this kept failing even after the previous 403 fix switched bare
    # `requests` calls over to ZenRows.
    #
    # The Chrome extension's own image fetch (fillVehicleForm.ts, uploading the
    # same URL to Facebook Marketplace) succeeds because it runs as a normal
    # `fetch()` from the dealer's own browser — a residential IP, not a proxy —
    # which is exactly what Peakhour is choosing not to block. `mode=auto`
    # asks ZenRows for Adaptive Stealth Mode: it tries the cheap default path
    # first and only escalates to premium (residential) proxies/JS rendering
    # when that gets blocked, and per ZenRows' billing model we're only
    # charged for whichever attempt actually succeeds — so this fixes Gumtree
    # image downloads without paying premium-proxy cost on every other
    # dealer/custom-domain image this same function downloads.
    if not settings.ZENROWS_API_KEY:
        raise ValueError("ZENROWS_API_KEY is not configured in the environment variables")
    client = ZenRowsClient(settings.ZENROWS_API_KEY)
    response = client.get(url, params={'mode': 'auto'}, timeout=timeout)
    response.raise_for_status()
    content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
        raise ValueError(f"Unexpected content type '{content_type}' for image URL {url}")
    return response.content


def content_hash_for(data):
    return hashlib.sha256(data).hexdigest()


def build_variant_bytes(data, sizes, quality):
    """
    sizes: {name: max_width}. Returns {name: (webp_bytes, width, height)}.
    Only ever downscales — a source narrower than a given size is kept as-is
    rather than upscaled (upscaling wastes storage/bandwidth without adding
    real detail).
    """
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        source = ImageOps.exif_transpose(source)
        if source.mode not in ('RGB', 'RGBA'):
            source = source.convert('RGBA' if 'A' in source.mode else 'RGB')

        variants = {}
        # Largest first so a single decoded+oriented `source` is reused for every
        # downscale instead of re-decoding per size.
        for name, max_width in sorted(sizes.items(), key=lambda item: -item[1]):
            image = source
            if source.width > max_width:
                ratio = max_width / float(source.width)
                new_size = (max_width, max(1, round(source.height * ratio)))
                image = source.resize(new_size, Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format='WEBP', quality=quality, method=6)
            variants[name] = (buffer.getvalue(), image.width, image.height)
        return variants


def build_upload_variant_bytes(data, max_width, quality):
    """One JPEG rendition for the Chrome extension to re-upload to Facebook
    Marketplace.

    FB Marketplace only accepts JPEG/PNG for listing photos — it rejects the
    WebP variants we serve to the storefront — so the extension needs a JPEG
    copy hosted on our own CORS-friendly CDN (otherwise it must proxy the
    full-size dealer original per photo on every publish, which is what caused
    the PARTIAL_IMAGE_UPLOAD timeouts). Downscale-only (never upscales); alpha is
    flattened onto white because JPEG has no alpha channel."""
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        source = ImageOps.exif_transpose(source)
        if source.mode in ('RGBA', 'LA') or (source.mode == 'P' and 'transparency' in source.info):
            source = source.convert('RGBA')
            background = Image.new('RGB', source.size, (255, 255, 255))
            background.paste(source, mask=source.split()[-1])
            source = background
        elif source.mode != 'RGB':
            source = source.convert('RGB')

        image = source
        if source.width > max_width:
            ratio = max_width / float(source.width)
            image = source.resize((max_width, max(1, round(source.height * ratio))), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=quality, optimize=True)
        return buffer.getvalue(), image.width, image.height


def s3_key_for(content_hash, size, ext='webp'):
    """Content-hash-first key layout: identical photos always resolve to the
    same key, so a re-upload of already-stored content is a harmless no-op
    overwrite rather than a duplicate object. Sharded by the first two hex
    chars to keep any single S3 prefix from growing unbounded. `ext` lets the
    JPEG upload variant sit alongside the WebP variants under the same hash."""
    prefix = settings.AWS_S3_VEHICLE_IMAGE_PREFIX.rstrip('/')
    return f"{prefix}/{content_hash[:2]}/{content_hash}/{size}.{ext}"


def _s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=settings.AWS_VEHICLE_IMAGE_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_VEHICLE_IMAGE_SECRET_ACCESS_KEY,
        region_name=settings.AWS_VEHICLE_IMAGE_REGION,
    )


def upload_variant(key, body_bytes, content_type='image/webp'):
    _s3_client().put_object(
        Bucket=settings.AWS_VEHICLE_IMAGE_BUCKET,
        Key=key,
        Body=body_bytes,
        ContentType=content_type,
        CacheControl='public, max-age=31536000, immutable',
    )


def delete_variants_from_s3(hosted_image):
    keys = [k for k in (hosted_image.thumbnail_image, hosted_image.medium_image, hosted_image.large_image, hosted_image.upload_image) if k]
    if not keys:
        return
    try:
        _s3_client().delete_objects(
            Bucket=settings.AWS_VEHICLE_IMAGE_BUCKET,
            Delete={'Objects': [{'Key': key} for key in keys]},
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error("Failed to delete S3 objects for HostedImage %s: %s", hosted_image.pk, exc)
        raise


def public_url_for(key):
    if not key:
        return None
    if settings.AWS_CLOUDFRONT_DOMAIN:
        return f"https://{settings.AWS_CLOUDFRONT_DOMAIN}/{key}"
    return f"https://{settings.AWS_VEHICLE_IMAGE_BUCKET}.s3.{settings.AWS_VEHICLE_IMAGE_REGION}.amazonaws.com/{key}"


def _make_and_upload_upload_variant(content_hash, image_bytes):
    """Build the FB-safe JPEG copy and put it in S3; return its key."""
    upload_bytes, _upload_w, _upload_h = build_upload_variant_bytes(
        image_bytes,
        settings.VEHICLE_IMAGE_SIZES['large'],
        settings.VEHICLE_IMAGE_UPLOAD_JPEG_QUALITY,
    )
    upload_key = s3_key_for(content_hash, 'upload', ext='jpg')
    upload_variant(upload_key, upload_bytes, content_type='image/jpeg')
    return upload_key


def get_or_create_ready_hosted_image(content_hash, source_url, image_bytes, build_upload_variant=True):
    """
    Returns (HostedImage, uploaded: bool). If a ready HostedImage already
    exists for this exact content, it's returned (almost) unchanged and the
    WebP variants are NOT re-encoded or re-uploaded — this is the dedup path
    that makes relisting the same photo (or reusing one across listings) free.

    `build_upload_variant` controls whether the extra FB-safe JPEG copy is
    produced. Only custom-domain listings ever use it (the extension serves it
    instead of proxying the original); Gumtree images never do, so the caller
    passes False for them and we skip the extra encode + S3 object entirely —
    no wasted work for Gumtree. If a photo first hosted for Gumtree (no JPEG) is
    later needed by a custom-domain listing, that call passes True and we lazily
    add just the JPEG to the existing row.
    """
    ready = HostedImage.objects.filter(content_hash=content_hash, status=HostedImage.STATUS_READY).first()
    if ready:
        if build_upload_variant and not ready.upload_image:
            ready.upload_image = _make_and_upload_upload_variant(content_hash, image_bytes)
            ready.save(update_fields=['upload_image', 'updated_at'])
        return ready, False

    variants = build_variant_bytes(image_bytes, settings.VEHICLE_IMAGE_SIZES, settings.VEHICLE_IMAGE_WEBP_QUALITY)
    keys = {}
    for size, (webp_bytes, width, height) in variants.items():
        key = s3_key_for(content_hash, size)
        upload_variant(key, webp_bytes)
        keys[size] = (key, width, height)

    # FB-safe JPEG copy for the extension's Marketplace upload (FB rejects our
    # WebP) — built only for custom-domain images that will actually use it.
    upload_key = (
        _make_and_upload_upload_variant(content_hash, image_bytes)
        if build_upload_variant else ''
    )

    large_key, large_width, large_height = keys['large']
    defaults = {
        'source_url': source_url,
        'thumbnail_image': keys['thumbnail'][0],
        'medium_image': keys['medium'][0],
        'large_image': large_key,
        'upload_image': upload_key,
        'width': large_width,
        'height': large_height,
        'file_size_bytes': len(image_bytes),
        'status': HostedImage.STATUS_READY,
    }
    try:
        with transaction.atomic():
            hosted, _created = HostedImage.objects.update_or_create(content_hash=content_hash, defaults=defaults)
    except IntegrityError:
        # Lost a race to another worker uploading the same brand-new content —
        # the S3 objects we just wrote are identical to theirs, so this is safe
        # to discard; just adopt the row they created.
        hosted = HostedImage.objects.get(content_hash=content_hash)
    return hosted, True


def sync_listing_images(listing, image_urls):
    """Fail-safe wrapper around _sync_listing_images.

    This runs inline inside the Gumtree and custom-domain scrape loops, right
    after the listing row has already been saved. Image hosting is a
    presentation nicety; scraping is the revenue path. An exception escaping
    here would abort the rest of the scrape loop and leave a profile
    half-processed, so anything that goes wrong is logged and swallowed —
    the listing itself is already safely persisted, and the next scrape
    re-attempts the slot reconciliation.
    """
    try:
        _sync_listing_images(listing, image_urls)
    except Exception:
        logger.exception(
            "sync_listing_images failed for listing id=%s — listing data is saved; "
            "image slots will be retried on the next scrape",
            getattr(listing, "pk", None),
        )


def _sync_listing_images(listing, image_urls):
    """
    Reconcile listing.image_slots against a freshly-scraped list of source
    URLs. Called every time a scraper sets/updates VehicleListing.images.

    - URLs already tracked for this listing are left untouched (their
      VehicleListingImage row keeps whatever status/hosted_image it has) —
      this is what stops an unchanged photo from being re-downloaded on
      every relist.
    - URLs no longer present are dropped; their post_delete signal (see
      signals.py) garbage-collects the underlying HostedImage's S3 objects
      once nothing else references them.
    - Brand-new URLs get a pending slot and a Celery task to process it.
    """
    # Collapse repeats first, preserving first-seen order. (listing, source_url)
    # is unique_together, so a scrape that hands back the same URL twice would
    # otherwise raise IntegrityError partway through the loop below; the
    # fail-safe wrapper swallows it and the listing is left with only the slots
    # created *before* the duplicate — fewer hosted photos than the listing
    # actually has, which is exactly what the extension reports as a partial
    # image upload.
    seen_urls = set()
    image_urls = [
        url for url in (image_urls or [])
        if url and not (url in seen_urls or seen_urls.add(url))
    ]
    existing_slots = {slot.source_url: slot for slot in listing.image_slots.all()}

    new_slot_ids = []
    for position, url in enumerate(image_urls):
        slot = existing_slots.get(url)
        if slot is None:
            slot = VehicleListingImage.objects.create(listing=listing, source_url=url, position=position)
            new_slot_ids.append(slot.pk)
        elif slot.position != position:
            slot.position = position
            slot.save(update_fields=['position', 'updated_at'])

    stale_urls = set(existing_slots) - set(image_urls)
    if stale_urls:
        VehicleListingImage.objects.filter(listing=listing, source_url__in=stale_urls).delete()

    if new_slot_ids:
        from .tasks import process_vehicle_listing_image_task

        # Stagger by 1 second per image within this listing (countdown=index)
        # rather than firing them all at once — a burst of concurrent requests
        # to the same dealer/Gumtree CDN is more likely to trip anti-bot/rate
        # blocking than the same requests spread out one per second.
        def _enqueue():
            for index, pk in enumerate(new_slot_ids):
                process_vehicle_listing_image_task.apply_async(args=[pk], countdown=index)

        transaction.on_commit(_enqueue)
