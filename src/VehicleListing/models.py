from django.db import models
from accounts.models import User
from decimal import Decimal

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
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    gumtree_url = models.ForeignKey(ListingUrl, on_delete=models.CASCADE,null=True,blank=True)
    gumtree_profile = models.ForeignKey(GumtreeProfileListing, on_delete=models.CASCADE,null=True,blank=True)
    facebook_profile = models.ForeignKey(FacebookProfileListing, on_delete=models.CASCADE,null=True,blank=True)
    custom_domain_url = models.ForeignKey(ListingUrl, on_delete=models.CASCADE,null=True,blank=True,related_name='custom_domain_vehicle_listings')
    custom_domain_profile = models.ForeignKey(CustomDomainProfileListing, on_delete=models.CASCADE,null=True,blank=True)
    
    list_id = models.CharField(max_length=255)
    year = models.CharField(max_length=255,null=True,blank=True)
    body_type = models.CharField(max_length=255,null=True,blank=True)
    fuel_type = models.CharField(max_length=255,null=True,blank=True)
    color = models.CharField(max_length=255,null=True,blank=True)
    variant = models.CharField(max_length=255,null=True,blank=True)
    make = models.CharField(max_length=100,null=True,blank=True)
    model = models.CharField(max_length=100,null=True,blank=True)
    price = models.CharField(max_length=255,null=True,blank=True)
    mileage = models.IntegerField(null=True,blank=True)
    # True when a custom-domain scrape could not determine a usable odometer
    # (missing or 0). Mileage is the tie-breaker the extension uses to tell
    # apart several cars that share a title; this flag marks the rows where
    # that signal isn't available so they aren't treated as distinguishable.
    # Gumtree rows always carry a parsed odometer (the scrape drops a listing
    # rather than store it blank), so this stays False for them.
    mileage_unavailable = models.BooleanField(default=False)
    # 17-character Vehicle Identification Number, captured from Gumtree's
    # "VIN" category field when a dealer has filled it in — optional because
    # not every Gumtree listing carries one. Needed for the separate VIN
    # database project: only listings with a VIN are eligible to display in
    # Google (per that project's requirement).
    vin = models.CharField(max_length=17, null=True, blank=True)
    exterior_colour = models.CharField(max_length=255,null=True,blank=True)
    interior_colour = models.CharField(max_length=255,null=True,blank=True)
    description = models.TextField(null=True,blank=True)
    condition = models.CharField(max_length=255,null=True,blank=True)
    transmission=models.CharField(max_length=255,null=True,blank=True)
    images = models.JSONField(null=True,blank=True)  # Store image URLs as JSON
    location = models.CharField(max_length=255,null=True,blank=True)
    url = models.URLField(null=True,blank=True)
    seller_profile_id = models.CharField(max_length=255,null=True,blank=True)
    # Facebook Marketplace listing ID — captured by the browser extension after publish,
    # used for targeted deletes (replaces fragile title-search deletes). Overwritten on each call.
    facebook_listing_id = models.CharField(max_length=128, null=True, blank=True)
    status = models.CharField(max_length=255, null=True)
    is_relist = models.BooleanField(default=False)
    is_changed = models.BooleanField(default=False)     # True when an existing listing's data was updated by the scraper
    is_listed = models.BooleanField(default=False)      # True once listing has been counted (prevent double-count)
    # True after Stripe metered overage for this listing was invoiced & recorded (idempotency).
    stripe_overage_reported = models.BooleanField(default=False)
    relist_count = models.IntegerField(default=0)       # how many times this specific listing has been relisted
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('2.00'))
    retry_count = models.IntegerField(default=0)
    listed_on = models.DateTimeField(null=True,blank=True)
    has_images=models.BooleanField(default=False)
    sales = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Guard against race-condition duplicates from concurrent background
        # scrapes (an initial POST overlapping with a cron-triggered re-scrape,
        # or rapid repeated POSTs). The orchestrator wraps create in
        # transaction.atomic and treats IntegrityError on this constraint as
        # "another thread won the race" — see custom_domain_scraper.py.
        unique_together = [("user", "list_id", "seller_profile_id")]

    def __str__(self):
        return f"{self.year} {self.make} {self.model}"
class FacebookListing(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    listing = models.ForeignKey(VehicleListing, on_delete=models.CASCADE)
    status = models.CharField(max_length=255, null=True)
    error_message = models.TextField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.listing.make} {self.listing.model}"
    
class RelistingFacebooklisting(models.Model):
    listing = models.ForeignKey(VehicleListing, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    relisting_date=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=255, null=True)
    last_relisting_status = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.listing.make} {self.listing.model}"
    

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
