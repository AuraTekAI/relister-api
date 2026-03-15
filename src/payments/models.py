from django.db import models
from django.utils import timezone
from accounts.models import User


class Plan(models.Model):
    name = models.CharField(max_length=100, unique=True)
    stripe_price_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_overage_price_id = models.CharField(max_length=255, blank=True, null=True)
    price_aud = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    listing_quota = models.IntegerField(null=True, blank=True)
    overage_rate_aud = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class DiscountCode(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('fixed', 'Fixed Amount'),
    ]

    code = models.CharField(max_length=50, unique=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_uses = models.IntegerField(null=True, blank=True)  # null = unlimited
    used_count = models.IntegerField(default=0)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        now = timezone.now()
        not_exhausted = self.max_uses is None or self.used_count < self.max_uses
        return self.is_active and self.valid_from <= now <= self.valid_until and not_exhausted

    def __str__(self):
        return self.code


class Subscription(models.Model):
    STATUS_CHOICES = [
        ('trial', 'Trial'),
        ('active', 'Active'),
        ('past_due', 'Past Due'),
        ('cancelled', 'Cancelled'),
        ('suspended', 'Suspended'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions',
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True, null=True)
    # Stores the Stripe subscription item ID for the metered overage price,
    # used when reporting usage via stripe.SubscriptionItem.create_usage_record()
    stripe_overage_subscription_item_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='trial')
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    # Reset to 0 each billing cycle via the customer.subscription.updated webhook
    listing_count = models.IntegerField(default=0)
    active_discount_code = models.ForeignKey(
        DiscountCode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions',
    )
    # True when user has requested cancellation but period hasn't ended yet.
    # Stripe: cancel_at_period_end=True. Access continues until current_period_end.
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} — {self.status}"


# Invoice model has been consolidated into VehicleListing.Invoice.
# All invoice functionality (billing, Stripe, GST, overage) is now in VehicleListing.Invoice.
