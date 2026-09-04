"""
Vehicle photo ingestion pipeline: download a scraped image once, convert it to
three WebP sizes, upload to our own S3 bucket, and record it so the storefront
API can serve it instead of the original Gumtree/dealer URL.

Call graph (lazy, publish-time ingestion — the default):
  gumtree_scraper.py / custom_domain_scraper.py
      -> sync_listing_images(listing, urls)      [diff against existing slots; slots stay 'pending']
  views.get_listing_images_status (extension calls it right before publishing ONE listing)
      -> ensure_listing_image_ingest(listing)    [claims that listing's slots, enqueues]
          -> tasks.process_vehicle_listing_image_task (Celery, per slot)
              -> download_image_bytes -> content_hash_for -> get_or_create_ready_hosted_image
                  -> build_variant_bytes -> upload_variant (S3)
(Set IMAGE_INGEST_ON_SCRAPE=True to restore eager enqueueing at scrape time.)

Deletion is the mirror image: VehicleListing.image_slots cascade-delete with
their listing, VehicleListing/signals.py notices each slot's post_delete and
enqueues tasks.gc_hosted_image_task, which removes the S3 objects once no
listing references that HostedImage any more.
"""
import hashlib
import io
import logging

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
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
    # which is exactly what Peakhour is choosing not to block. For Gumtree image
    # URLs we therefore ask ZenRows for a premium/residential AU proxy straight
    # away: the datacenter path is *always* blocked, so `mode=auto` only wastes a
    # failing request before escalating. For all other dealer/custom-domain URLs we
    # keep `mode=auto` so we only pay premium-proxy cost when the cheap path is
    # actually blocked.
    if not settings.ZENROWS_API_KEY:
        raise ValueError("ZENROWS_API_KEY is not configured in the environment variables")

    is_gumtree_image = 'images.gumtree.com.au' in url
    params = (
        {'premium_proxy': 'true', 'proxy_country': 'au'}
        if is_gumtree_image
        else {'mode': 'auto'}
    )

    client = ZenRowsClient(settings.ZENROWS_API_KEY)
    response = client.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    # ZenRows' own response envelope's Content-Type does NOT reliably reflect
    # the origin resource's real type — confirmed live against a production
    # Gumtree image: envelope said `text/plain`, but the body was a genuine
    # JPEG (correct magic bytes, byte-for-byte length matching Zr-Content-Length)
    # and ZenRows had separately reported the true type via `Zr-Content-Type:
    # image/jpeg`. Trusting the envelope header here was rejecting real,
    # successfully-fetched photos as "unexpected content type" and permanently
    # failing them (this is NOT a retryable RequestException, so it failed on
    # the very first attempt with no retries). Prefer ZenRows' own
    # `Zr-Content-Type` when present; fall back to the envelope header
    # otherwise (e.g. requests that didn't go through ZenRows' proxying, or a
    # future API response shape that omits it).
    content_type = (
        response.headers.get('Zr-Content-Type')
        or response.headers.get('Content-Type')
        or ''
    ).split(';')[0].strip().lower()
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


# Cached per credential/region triple. public_url_for presigns one URL per
# photo, so an extension home-page response asks for a client dozens of times;
# building a fresh boto3 client each time costs ~100ms of session/config setup
# apiece. Keying on the settings triple (rather than a bare module global) keeps
# override_settings in tests honest — a changed bucket region builds its own
# client instead of silently reusing the first one.
_s3_client_cache = {}


def _s3_client():
    cache_key = (
        settings.AWS_VEHICLE_IMAGE_ACCESS_KEY_ID,
        settings.AWS_VEHICLE_IMAGE_SECRET_ACCESS_KEY,
        settings.AWS_VEHICLE_IMAGE_REGION,
    )
    client = _s3_client_cache.get(cache_key)
    if client is None:
        client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_VEHICLE_IMAGE_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_VEHICLE_IMAGE_SECRET_ACCESS_KEY,
            region_name=settings.AWS_VEHICLE_IMAGE_REGION,
            # Both settings matter only for generate_presigned_url, and both are
            # wrong by default. Botocore still presigns with SigV2 unless told
            # otherwise, which signs against the global us-east-1 endpoint and
            # in path style: the bucket is in ap-southeast-2, so that URL comes
            # back as a cross-region redirect rather than the image. SigV4 plus
            # virtual addressing yields the regional
            # https://<bucket>.s3.<region>.amazonaws.com/<key>?X-Amz-... form
            # that resolves directly. Harmless for put_object/delete_objects,
            # which already resolved the regional endpoint on their own.
            config=BotoConfig(
                signature_version='s3v4',
                s3={'addressing_style': 'virtual'},
            ),
        )
        _s3_client_cache[cache_key] = client
    return client


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
    """Fetchable URL for one S3 object.

    CloudFront path (AWS_CLOUDFRONT_DOMAIN set) is unchanged: a plain CDN URL,
    no signing, because the distribution fronts the bucket with its own access.

    Without CloudFront we sign the URL (unless VEHICLE_IMAGE_PRESIGN_URLS is
    off). Both consumers fetch these URLs anonymously from outside our
    infrastructure — the extension home page as an <img>, and the Facebook
    publish as a `fetch()` issued from the Facebook tab itself (see
    fillVehicleForm.ts uploadPhotos, which downloads each photo and uploads the
    blob). Signing makes that work whatever the bucket policy happens to be.
    See settings.VEHICLE_IMAGE_PRESIGN_URLS for why it is belt-and-braces
    rather than strictly required today.

    Returns None if signing fails, which every caller already treats as "not
    hosted yet" and falls back to the raw scraped source URL for.
    """
    if not key:
        return None
    if settings.AWS_CLOUDFRONT_DOMAIN:
        return f"https://{settings.AWS_CLOUDFRONT_DOMAIN}/{key}"
    if not getattr(settings, 'VEHICLE_IMAGE_PRESIGN_URLS', True):
        return f"https://{settings.AWS_VEHICLE_IMAGE_BUCKET}.s3.{settings.AWS_VEHICLE_IMAGE_REGION}.amazonaws.com/{key}"
    try:
        return _s3_client().generate_presigned_url(
            'get_object',
            Params={'Bucket': settings.AWS_VEHICLE_IMAGE_BUCKET, 'Key': key},
            ExpiresIn=settings.VEHICLE_IMAGE_PRESIGNED_URL_TTL,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error("Failed to presign S3 URL for key %s: %s", key, exc)
        return None


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

    # ✓ CRITICAL FIX: Upload ALL WebP variants FIRST, with explicit error handling.
    # If ANY upload fails (S3 permissions, network, credentials), re-raise the exception
    # so Celery retries the entire task. Do NOT create HostedImage with partial/incomplete keys.
    keys = {}
    for size, (webp_bytes, width, height) in variants.items():
        key = s3_key_for(content_hash, size)
        try:
            upload_variant(key, webp_bytes)  # ← Raises exception if upload fails
            keys[size] = (key, width, height)
        except Exception as e:
            # S3 upload failed (credentials, permissions, network, etc.)
            # Re-raise so Celery retries the entire task.
            # This prevents partial HostedImage creation with incomplete S3 keys.
            logger.error(
                "Failed to upload WebP variant for content_hash=%s, size=%s, key=%s: %s",
                content_hash, size, key, str(e),
                exc_info=True
            )
            raise

    # FB-safe JPEG copy for the extension's Marketplace upload (FB rejects our
    # WebP) — built only for custom-domain images that will actually use it.
    # Same error handling: if upload fails, re-raise to prevent partial HostedImage.
    upload_key = ''
    if build_upload_variant:
        try:
            upload_key = _make_and_upload_upload_variant(content_hash, image_bytes)
        except Exception as e:
            # S3 upload failed for JPEG variant. Re-raise so Celery retries.
            logger.error(
                "Failed to upload JPEG variant for content_hash=%s: %s",
                content_hash, str(e),
                exc_info=True
            )
            raise

    # ✓ VALIDATED: All S3 uploads succeeded. Now safe to create HostedImage with
    # complete S3 keys. If creation fails due to race condition (IntegrityError),
    # another worker created it first; we catch that and use their record.
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
    # TEMPORARY: settings.BYPASS_GUMTREE_IMAGE_HOSTING. Skip ingestion
    # entirely for Gumtree listings — no VehicleListingImage slots get
    # created and no Celery download/S3-upload task ever gets enqueued, so
    # the hosted pipeline does zero work for Gumtree photos. Custom-domain
    # listings (gumtree_profile_id is None) are unaffected. See the setting's
    # docstring in settings.py for how to revert.
    if getattr(settings, 'BYPASS_GUMTREE_IMAGE_HOSTING', False) and getattr(listing, 'gumtree_profile_id', None):
        logger.info(
            "BYPASS_GUMTREE_IMAGE_HOSTING enabled: skipping image slot creation for "
            "Gumtree listing id=%s (gumtree_profile_id=%s)",
            getattr(listing, "pk", None),
            getattr(listing, 'gumtree_profile_id', None),
        )
        return
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
            try:
                # ✓ CREATE with explicit conflict handling: if a race condition
                # creates a duplicate slot between our .all() fetch and this create,
                # catch it and get the newly-created one instead of silently dropping
                # this image from the listing.
                slot = VehicleListingImage.objects.create(
                    listing=listing,
                    source_url=url,
                    position=position
                )
                new_slot_ids.append(slot.pk)
            except IntegrityError as e:
                # Race condition: another process created this slot between our
                # fetch above and this create. Get it and use it.
                logger.warning(
                    "IntegrityError creating image slot for listing id=%s, URL=%s "
                    "(likely race condition); attempting to use existing slot",
                    listing.id, url
                )
                try:
                    slot = VehicleListingImage.objects.get(listing=listing, source_url=url)
                    if slot.position != position:
                        slot.position = position
                        slot.save(update_fields=['position', 'updated_at'])
                    new_slot_ids.append(slot.pk)
                except VehicleListingImage.DoesNotExist:
                    # Slot disappeared between our error and get attempt. Skip this image.
                    logger.error(
                        "Failed to create or retrieve image slot for listing id=%s, "
                        "URL=%s after IntegrityError; image will be missing",
                        listing.id, url
                    )
        elif slot.position != position:
            slot.position = position
            slot.save(update_fields=['position', 'updated_at'])

    stale_urls = set(existing_slots) - set(image_urls)
    if stale_urls:
        VehicleListingImage.objects.filter(listing=listing, source_url__in=stale_urls).delete()

    # LAZY PIPELINE (default): slots are created as bookkeeping only — no
    # download/S3 upload happens at scrape time. Ingestion for a listing is
    # triggered on demand, right before THAT listing is published, via
    # ensure_listing_image_ingest() (called by the images-status endpoint the
    # extension polls before each publish). This is deliberate: scraping 50
    # products × 20 photos used to enqueue ~1,000 downloads/uploads upfront,
    # storing images for listings that might never be published. Set
    # IMAGE_INGEST_ON_SCRAPE=True in the env to restore the old eager
    # behaviour (no code deploy needed).
    if new_slot_ids and getattr(settings, 'IMAGE_INGEST_ON_SCRAPE', False):
        from .tasks import process_vehicle_listing_image_task

        # Stagger by 1 second per image within this listing (countdown=index)
        # rather than firing them all at once — a burst of concurrent requests
        # to the same dealer/Gumtree CDN is more likely to trip anti-bot/rate
        # blocking than the same requests spread out one per second.
        def _enqueue():
            for index, pk in enumerate(new_slot_ids):
                process_vehicle_listing_image_task.apply_async(args=[pk], countdown=index)

        transaction.on_commit(_enqueue)


# A slot claimed as queued/processing whose task apparently died (worker
# restart, lost broker message) is re-claimable after this long. Generous on
# purpose: the ingest task's own retry backoff can legitimately keep a slot
# in-flight for tens of minutes (retry_backoff_max=600 × max_retries=5).
STALE_INGEST_AGE = timedelta(hours=1)


def ensure_listing_image_ingest(listing):
    """Queue the S3 ingest tasks for THIS listing's unprocessed photos.

    The lazy-pipeline trigger: called (repeatedly — it's idempotent) by the
    images-status endpoint when the extension is about to publish `listing`.
    Only this listing's photos are downloaded/stored; nothing is queued for
    any other listing, which is what keeps S3 usage proportional to what
    actually gets published.

    Claims slots atomically (pending → queued) so concurrent polls can't
    double-enqueue, and re-claims queued/processing slots untouched for over
    STALE_INGEST_AGE — a task lost to a worker restart must not block the
    listing forever. Returns how many slots were (re-)queued.
    """
    from .models import VehicleListingImage
    from .tasks import process_vehicle_listing_image_task

    now = timezone.now()
    stale_cutoff = now - STALE_INGEST_AGE
    # Claim never-started (pending) slots and lost-in-flight (stale
    # queued/processing) ones. FAILED is deliberately TERMINAL and NOT
    # re-claimed here: the ingest task already retries transient upload errors
    # internally (autoretry_for, up to max_retries) before marking a slot
    # FAILED — "try again". Once a slot is FAILED it is skipped, so ingestion
    # can actually COMPLETE (in_flight → 0) instead of a permanently-bad image
    # being re-queued forever and blocking the publish. The publish then goes
    # ahead with the images that DID reach S3.
    claimable_pks = list(
        VehicleListingImage.objects.filter(listing=listing).filter(
            Q(status=VehicleListingImage.STATUS_PENDING)
            | Q(status__in=[VehicleListingImage.STATUS_QUEUED,
                            VehicleListingImage.STATUS_PROCESSING],
                updated_at__lt=stale_cutoff)
        ).values_list('pk', flat=True)
    )
    if not claimable_pks:
        return 0

    VehicleListingImage.objects.filter(pk__in=claimable_pks).update(
        status=VehicleListingImage.STATUS_QUEUED, updated_at=now
    )

    def _enqueue():
        # Same 1s-per-image stagger rationale as the (optional) eager path.
        for index, pk in enumerate(claimable_pks):
            process_vehicle_listing_image_task.apply_async(args=[pk], countdown=index)

    transaction.on_commit(_enqueue)
    return len(claimable_pks)
