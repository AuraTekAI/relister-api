from django.db import models
from django.db.models import Q
from accounts.models import User
from decimal import Decimal


class Vehicle(models.Model):
    """One record per physical vehicle, shared across every Auto Relister user's listings."""
    vin = models.CharField(max_length=17, null=True, blank=True)
    make = models.CharField(max_length=100, null=True, blank=True)
    model = models.CharField(max_length=100, null=True, blank=True)
    # Trim/spec level (e.g. "Ascent Sport", "SX"). Every scraper/adapter already
    # extracts this into its result dict — it just wasn't persisted anywhere
    # until the price-estimation feature needed it for like-for-like matching.
    variant = models.CharField(max_length=255, null=True, blank=True)
    year = models.CharField(max_length=255, null=True, blank=True)
    mileage = models.IntegerField(null=True, blank=True)
    transmission = models.CharField(max_length=255, null=True, blank=True)
    fuel_type = models.CharField(max_length=255, null=True, blank=True)
    body_type = models.CharField(max_length=255, null=True, blank=True)
    color = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['vin'], condition=Q(vin__isnull=False), name='uniq_vehicle_vin'),
        ]

    def __str__(self):
        return f"{self.year} {self.make} {self.model}"


class VehicleImage(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='images')
    image_url = models.URLField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.image_url

class ListingUrl(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField(null=True,blank=True)
    listing_id = models.CharField(max_length=255,null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=255, null=True)
    error_message = models.TextField(null=True)
    def __str__(self):
        return f"{self.url}"
    
class FacebookUserCredentials(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    session_cookie = models.JSONField(null=True,blank=True,default=dict)
    status = models.BooleanField(default=False)
    status_reminder = models.BooleanField(default=False)
    retry_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class GumtreeProfileListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField(null=True,blank=True)
    total_listings = models.IntegerField(null=True,blank=True)
    processed_listings = models.IntegerField(null=True,blank=True)
    status = models.CharField(max_length=255, null=True,blank=True)
    profile_id = models.CharField(max_length=255, null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.url}"

class CustomDomainProfileListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField(null=True,blank=True)
    domain = models.CharField(max_length=255, null=True,blank=True)
    total_listings = models.IntegerField(null=True,blank=True)
    processed_listings = models.IntegerField(null=True,blank=True)
    status = models.CharField(max_length=255, null=True,blank=True)
    profile_id = models.CharField(max_length=255, null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.url}"

class FacebookProfileListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    url = models.URLField(null=True,blank=True)
    total_listings = models.IntegerField(null=True,blank=True)
    processed_listings = models.IntegerField(null=True,blank=True)
    status = models.CharField(max_length=255, null=True)
    profile_id = models.CharField(max_length=255, null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.url}"
class VehicleListing(models.Model):
    # Lifecycle status (separate from the `status` field below, which is the
    # pre-existing pending/failed/completed/sold scrape-and-publish state
    # machine relied on throughout the scrapers/admin/extension). This is a
    # purpose-built, business-facing "is this car for sale right now" field
    # that never changes meaning and is kept in sync automatically wherever
    # `status` already transitions — see utils.mark_listing_sold /
    # utils.withdraw_listing / utils.reactivate_listing.
    LIFECYCLE_ACTIVE = 'active'
    LIFECYCLE_SOLD = 'sold'
    LIFECYCLE_WITHDRAWN = 'withdrawn'
    LIFECYCLE_STATUS_CHOICES = [
        (LIFECYCLE_ACTIVE, 'Active'),
        (LIFECYCLE_SOLD, 'Sold'),
        (LIFECYCLE_WITHDRAWN, 'Withdrawn'),
    ]

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='listings')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    price = models.CharField(max_length=255,null=True,blank=True)
    description = models.TextField(null=True,blank=True)
    status = models.CharField(max_length=255, null=True)
    gumtree_url = models.ForeignKey(ListingUrl, on_delete=models.CASCADE,null=True,blank=True)
    facebook_url = models.URLField(max_length=500, null=True, blank=True)
    seller_profile_id = models.CharField(max_length=255,null=True,blank=True)
    listed_on = models.DateTimeField(null=True,blank=True)
    relist_count = models.IntegerField(default=0)       # how many times this specific listing has been relisted
    retry_count = models.IntegerField(default=0)
    # Set once, the first time this listing is ever published (mirrors the
    # existing `listed_on is None` first-publish check in
    # update_vehicle_listing_listed_on) — and never touched again after that,
    # unlike `listed_on` which is overwritten on every relist.
    first_listed_at = models.DateTimeField(null=True, blank=True)
    # Set when the listing stops being for sale (sold or withdrawn); cleared
    # back to None if it's later reactivated/relisted.
    delisted_at = models.DateTimeField(null=True, blank=True)
    # (delisted_at - first_listed_at) in whole days, computed only when the
    # listing is actually sold (not withdrawn). Cleared on reactivation.
    days_to_sell = models.PositiveIntegerField(null=True, blank=True)
    lifecycle_status = models.CharField(
        max_length=20, choices=LIFECYCLE_STATUS_CHOICES, default=LIFECYCLE_ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Note: Guard against race-condition duplicates is handled at the application layer
        # via transaction.atomic() in scraper code. See custom_domain_scraper.py.
        # The list_id field (used by scrapers) does not currently exist as a separate field
        # on VehicleListing; listings are uniquely identified via gumtree_url relationship.
        pass

    def __str__(self):
        return f"{self.vehicle} ({self.user_id})"
class FacebookListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    listing = models.ForeignKey(VehicleListing, on_delete=models.CASCADE)
    status = models.CharField(max_length=255, null=True)
    error_message = models.TextField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.listing.vehicle.make} {self.listing.vehicle.model}"

class RelistingFacebooklisting(models.Model):
    listing = models.ForeignKey(VehicleListing, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    relisting_date=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=255, null=True)
    last_relisting_status = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.listing.vehicle.make} {self.listing.vehicle.model}"


class FacebookListingSnapshot(models.Model):
    """
    Latest snapshot of a user's LIVE Facebook Marketplace listings, pushed by the
    browser extension roughly every hour while it is running (gumtree + custom-domain
    modes). One row per (user, fb_listing_id); the whole set for a user is replaced
    on each sync. Powers the admin dashboard: which FB listings exist, how long since
    each was published on Facebook, which backend VehicleListing it matches, whether
    it is aged (older than the relist threshold), and duplicates.
    """
    MODE_CHOICES = [
        ('gumtree', 'Gumtree'),
        ('customdomain', 'Custom Domain'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fb_snapshots')
    fb_listing_id = models.CharField(max_length=64)
    fb_url = models.URLField(max_length=500, null=True, blank=True)
    title = models.CharField(max_length=500, null=True, blank=True)
    price = models.CharField(max_length=64, null=True, blank=True)
    # When the CURRENT Facebook listing was published (Facebook's own creationTime).
    fb_published_at = models.DateTimeField(null=True, blank=True)
    days_on_facebook = models.IntegerField(null=True, blank=True)
    # The backend VehicleListing this FB listing is matched to (null if unmatched).
    matched_listing = models.ForeignKey(
        VehicleListing, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='fb_snapshots'
    )
    is_aged = models.BooleanField(default=False)      # older than the relist threshold
    is_duplicate = models.BooleanField(default=False) # part of a same-title duplicate group
    duplicate_count = models.IntegerField(default=1)  # how many FB listings share this title
    mode = models.CharField(max_length=32, choices=MODE_CHOICES, null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'fb_listing_id')
        indexes = [
            models.Index(fields=['user', 'synced_at']),
            models.Index(fields=['user', 'is_aged']),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.fb_listing_id} {self.title}"


class UnpublishedListingSnapshot(models.Model):
    """
    Backend VehicleListings that are NOT currently on Facebook, together with the
    EXACT reason the extension skipped or couldn't publish them, pushed by the
    extension alongside the Facebook snapshot. Whole-set replace per user on each
    sync (mirrors FacebookListingSnapshot). Powers the admin dashboard's
    "which listings are not published — and why" view.
    """
    REASON_CHOICES = [
        ('SOLD', 'Sold on source'),
        ('INSUFFICIENT_IMAGES', 'Fewer than 2 images'),
        ('LOCATION_MISSING', 'No dealer location'),
        ('FAILED_HIDDEN', 'Failed repeatedly — hidden'),
        ('FAILED_COOLDOWN', 'In failure cooldown'),
        ('QUOTA_REACHED', 'Daily publish limit reached'),
        ('PENDING', 'Queued — not yet published'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='unpublished_snapshots')
    # The backend VehicleListing that isn't on Facebook. SET_NULL so a deleted
    # listing doesn't drop the (already stale-by-next-sync) snapshot row mid-cycle.
    listing = models.ForeignKey(
        VehicleListing, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='unpublished_snapshots'
    )
    title = models.CharField(max_length=500, null=True, blank=True)
    price = models.CharField(max_length=64, null=True, blank=True)
    images_count = models.IntegerField(default=0)
    # Machine reason code (see REASON_CHOICES) + a human-readable detail string.
    reason = models.CharField(max_length=40, choices=REASON_CHOICES, null=True, blank=True)
    reason_detail = models.CharField(max_length=255, null=True, blank=True)
    mode = models.CharField(max_length=32, choices=FacebookListingSnapshot.MODE_CHOICES, null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'synced_at']),
            models.Index(fields=['user', 'reason']),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.listing_id} {self.title} ({self.reason})"


class ExtensionSyncStatus(models.Model):
    """
    One row per dealer, upserted by the extension on EVERY sync — including when
    Facebook can't be loaded (verification wall, not logged in, rate limited).
    This is what makes a dealer appear on the admin dashboard even when they have
    zero Facebook listings, so operators can see WHICH dealers are broken and why.
    """
    STATUS_CHOICES = [
        ('ok', 'OK'),
        ('verification_required', 'Facebook verification required'),
        ('fb_error', 'Facebook load error'),
        ('rate_limited', 'Rate limited'),
        ('no_facebook', 'Not logged in to Facebook'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='ext_sync_status')
    mode = models.CharField(max_length=32, choices=FacebookListingSnapshot.MODE_CHOICES, null=True, blank=True)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default='ok')
    status_detail = models.CharField(max_length=255, null=True, blank=True)
    fb_count = models.IntegerField(default=0)
    unpublished_count = models.IntegerField(default=0)
    extension_version = models.CharField(max_length=32, null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'synced_at'])]

    def __str__(self):
        return f"{self.user_id} {self.status} @ {self.synced_at:%Y-%m-%d %H:%M}"


class FBVerificationEvent(models.Model):
    """
    Audit trail of Facebook verification wall detections and clearances.

    When the extension detects that Facebook is requiring account verification,
    it immediately POSTs to the backend, creating a 'detected' event. When
    verification is cleared (by successful publish or user action), a 'cleared'
    event is recorded. This enables:
    - Admin visibility: "which dealers are blocked and for how long?"
    - Metrics: "how often do verification walls appear?"
    - Audit trail: when and why was each dealer blocked/unblocked

    The companion ExtensionSyncStatus.status='verification_required' is the
    real-time state; this model is the event log.
    """
    STATUS_CHOICES = [
        ('detected', 'Verification wall detected'),
        ('cleared', 'Verification wall cleared'),
    ]
    WALL_TYPE_CHOICES = [
        ('checkpoint', 'Checkpoint'),
        ('confirm', 'Email/Phone confirmation'),
        ('disabled', 'Account disabled/restricted'),
        ('id_verify', 'Identity verification'),
        ('unknown', 'Unknown wall type'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fb_verification_events')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    wall_type = models.CharField(max_length=40, choices=WALL_TYPE_CHOICES, null=True, blank=True)
    # Free-text reason from the extension (e.g. for eligibility-based blocks that aren't wall detections)
    reason = models.CharField(max_length=255, null=True, blank=True)
    # Timestamp when the wall was detected (set by extension)
    detected_at = models.DateTimeField()
    # Backend-side timestamp
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status', 'created_at']),
            models.Index(fields=['status', 'created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user_id} {self.status} ({self.wall_type}) @ {self.created_at:%Y-%m-%d %H:%M}"


class Invoice(models.Model):
    STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('unpaid', 'Unpaid'),
        ('overdue', 'Overdue'),
    ]

    # Sequential invoice number, e.g. INV-2025-0001
    invoice_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    # Legacy field kept for backward compatibility with old invoice records
    invoice_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoices')
    # FK to payments.Subscription — set via string reference to avoid circular imports
    subscription = models.ForeignKey(
        'payments.Subscription',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='billing_invoices',
    )
    billing_period_start = models.DateTimeField(null=True, blank=True)
    billing_period_end = models.DateTimeField(null=True, blank=True)

    # Snapshots at billing time (plan may change later)
    plan_name = models.CharField(max_length=100, blank=True, default='')
    base_plan_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    included_listings = models.IntegerField(default=0)
    relist_cycles = models.IntegerField(default=4)

    # Overage
    overage_listings = models.IntegerField(default=0)
    overage_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overage_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Discount
    discount_code = models.ForeignKey(
        'payments.DiscountCode',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='billing_invoices',
    )
    # Snapshot of the discount code string at billing time — preserved even if DiscountCode is deleted
    discount_code_str = models.CharField(max_length=50, blank=True, default='')
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Totals
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Legacy details field kept for old invoice records
    details = models.TextField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='unpaid')
    stripe_invoice_id = models.CharField(max_length=255, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.invoice_number or self.invoice_id}"


class HostedImage(models.Model):
    """
    One physical photo, permanently stored in our own S3 bucket in three WebP
    sizes, keyed by the sha256 of the originally-downloaded bytes. Content-hash
    keying means the same photo — e.g. a relisted car whose Gumtree/dealer CDN
    URL rotated but the pixels didn't, or a stock photo reused across listings —
    is only ever downloaded, converted and uploaded once, no matter how many
    VehicleListingImage rows end up pointing at it.
    """
    STATUS_PENDING = 'pending'
    STATUS_READY = 'ready'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_READY, 'Ready'),
        (STATUS_FAILED, 'Failed'),
    ]

    content_hash = models.CharField(max_length=64, unique=True, db_index=True)
    # First source URL this content was ever seen at — kept for audit/debugging only.
    source_url = models.URLField(max_length=1000, null=True, blank=True)
    thumbnail_image = models.CharField(max_length=512, blank=True, default='')
    medium_image = models.CharField(max_length=512, blank=True, default='')
    large_image = models.CharField(max_length=512, blank=True, default='')
    # FB-safe JPEG rendition (same max width as `large`) that the Chrome
    # extension re-uploads to Facebook Marketplace, which rejects WebP. Blank
    # for images processed before this variant existed — the extension
    # serializer falls back to the image proxy for those until backfilled
    # (manage.py backfill_upload_variants).
    upload_image = models.CharField(max_length=512, blank=True, default='')
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    file_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.content_hash

    def url_for(self, size):
        """Public URL (CloudFront if configured, else direct S3) for one of
        'thumbnail' / 'medium' / 'large'. Deferred import to avoid a
        models <-> image_pipeline circular import."""
        from .image_pipeline import public_url_for
        key = {
            'thumbnail': self.thumbnail_image,
            'medium': self.medium_image,
            'large': self.large_image,
        }.get(size)
        return public_url_for(key)

    def upload_url(self):
        """Public URL for the FB-safe JPEG upload variant, or None if this image
        predates that variant (and hasn't been backfilled yet)."""
        from .image_pipeline import public_url_for
        return public_url_for(self.upload_image) if self.upload_image else None


class VehicleListingImage(models.Model):
    """
    Ordered slot for one of a listing's photos, tracking ingestion from the raw
    scraped source_url through to the shared HostedImage it resolves to.
    One row per (listing, source_url) so a relist that reports the same source
    URL again is a no-op here — sync_listing_images() (see image_pipeline.py)
    only creates rows for URLs it hasn't seen before on this listing, which is
    what guarantees an unchanged photo is never re-downloaded or re-enqueued.
    """
    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_READY = 'ready'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_READY, 'Ready'),
        (STATUS_FAILED, 'Failed'),
    ]

    listing = models.ForeignKey(VehicleListing, on_delete=models.CASCADE, related_name='image_slots')
    source_url = models.URLField(max_length=1000)
    position = models.PositiveIntegerField(default=0)
    hosted_image = models.ForeignKey(
        HostedImage, on_delete=models.SET_NULL, null=True, blank=True, related_name='listing_links'
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    error_message = models.TextField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('listing', 'source_url')]
        ordering = ['position']
        indexes = [
            models.Index(fields=['listing', 'position'], name='vl_vli_listing_position_idx'),
            models.Index(fields=['status'], name='vl_vli_status_idx'),
        ]

    def __str__(self):
        return f"{self.listing_id}:{self.position} ({self.status})"
