from rest_framework import serializers
from django.utils import timezone
from .models import Plan, Subscription, DiscountCode
from VehicleListing.models import Invoice


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        # stripe_price_id and stripe_overage_price_id are intentionally excluded
        fields = [
            'id',
            'name',
            'price_aud',
            'listing_quota',
            'overage_rate_aud',
            'is_active',
            'created_at',
        ]


# ---------------------------------------------------------------------------
# TICKET-010: Subscription / Trial status
# ---------------------------------------------------------------------------

class SubscriptionStatusSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)
    days_remaining = serializers.SerializerMethodField()
    listing_quota = serializers.SerializerMethodField()
    overage_rate_aud = serializers.SerializerMethodField()
    trial_end_date = serializers.SerializerMethodField()
    account_status = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'id',
            'plan',
            'status',
            'account_status',
            'current_period_start',
            'current_period_end',
            'trial_end_date',
            'days_remaining',
            'listing_count',
            'listing_quota',
            'overage_rate_aud',
            'cancel_at_period_end',
            'cancelled_at',
        ]
        read_only_fields = [
            'id', 'status', 'current_period_start', 'current_period_end',
            'listing_count', 'cancel_at_period_end', 'cancelled_at',
        ]

    def get_days_remaining(self, obj):
        if obj.current_period_end:
            delta = obj.current_period_end - timezone.now()
            return max(delta.days, 0)
        return None

    def get_listing_quota(self, obj):
        if obj.plan:
            return obj.plan.listing_quota
        return None

    def get_overage_rate_aud(self, obj):
        if obj.plan:
            return str(obj.plan.overage_rate_aud) if obj.plan.overage_rate_aud else None
        return None

    def get_trial_end_date(self, obj):
        return obj.user.trial_end_date

    def get_account_status(self, obj):
        return obj.user.account_status


class TrialStatusSerializer(serializers.Serializer):
    """
    Returned when the user has no Subscription record yet —
    derives status from the User model trial fields.
    """
    status = serializers.CharField()
    account_status = serializers.CharField()
    days_remaining = serializers.IntegerField(allow_null=True)
    trial_end_date = serializers.DateTimeField(allow_null=True)
    trial_start_date = serializers.DateTimeField(allow_null=True)
    listing_count = serializers.IntegerField()
    plan = serializers.DictField(allow_null=True)
    listing_quota = serializers.IntegerField(allow_null=True)
    overage_rate_aud = serializers.DecimalField(
        max_digits=10, decimal_places=2, allow_null=True
    )


# ---------------------------------------------------------------------------
# TICKET-011: Usage tracker
# ---------------------------------------------------------------------------

class UsageSerializer(serializers.Serializer):
    active_listing_count = serializers.IntegerField()
    relist_cycles_this_month = serializers.IntegerField()
    listings_used = serializers.IntegerField()
    listing_quota = serializers.IntegerField(allow_null=True)
    usage_percentage = serializers.FloatField(allow_null=True)
    overage_count = serializers.IntegerField()
    overage_rate = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    overage_amount = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    period_start = serializers.DateTimeField(allow_null=True)
    period_end = serializers.DateTimeField(allow_null=True)


# ---------------------------------------------------------------------------
# TICKET-012: Invoices
# ---------------------------------------------------------------------------

class InvoiceListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = [
            'id',
            'invoice_number',
            'created_at',
            'billing_period_start',
            'billing_period_end',
            'total_amount',
            'status',
        ]


class InvoiceDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = [
            'id',
            'invoice_number',
            'billing_period_start',
            'billing_period_end',
            'plan_name',
            'base_plan_charge',
            'included_listings',
            'relist_cycles',
            'overage_listings',
            'overage_rate',
            'overage_charge',
            'discount_code_str',
            'discount_amount',
            'subtotal',
            'gst_amount',
            'total_amount',
            'status',
            'stripe_invoice_id',
            'created_at',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        try:
            overage_listings = int(data.get('overage_listings') or 0)
            overage_charge = str(data.get('overage_charge') or '0')
            if overage_listings <= 0 and overage_charge in ('0', '0.0', '0.00'):
                data.pop('overage_listings', None)
                data.pop('overage_rate', None)
                data.pop('overage_charge', None)
        except Exception:
            pass
        return data


class DiscountCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscountCode
        fields = ['id', 'code', 'discount_type', 'discount_value']


# ---------------------------------------------------------------------------
# TICKET-018: Admin discount code management serializer
# ---------------------------------------------------------------------------

class AdminDiscountCodeSerializer(serializers.ModelSerializer):
    is_valid_now = serializers.SerializerMethodField()

    class Meta:
        model = DiscountCode
        fields = [
            'id',
            'code',
            'discount_type',
            'discount_value',
            'max_uses',
            'used_count',
            'valid_from',
            'valid_until',
            'is_active',
            'is_valid_now',
            'stripe_coupon_id',
            'created_at',
        ]
        read_only_fields = ['id', 'used_count', 'is_valid_now', 'stripe_coupon_id', 'created_at']

    def get_is_valid_now(self, obj):
        return obj.is_valid()

    def validate_discount_value(self, value):
        if value <= 0:
            raise serializers.ValidationError("discount_value must be greater than 0.")
        return value

    def validate(self, attrs):
        discount_type = attrs.get('discount_type', getattr(self.instance, 'discount_type', None))
        discount_value = attrs.get('discount_value', getattr(self.instance, 'discount_value', None))
        if discount_type == 'percentage' and discount_value is not None and discount_value > 100:
            raise serializers.ValidationError({'discount_value': "Percentage discount cannot exceed 100."})

        max_uses = attrs.get('max_uses')
        if max_uses is not None and self.instance is not None:
            if max_uses < self.instance.used_count:
                raise serializers.ValidationError(
                    {'max_uses': f"max_uses ({max_uses}) cannot be less than current used_count ({self.instance.used_count})."}
                )

        return attrs


# ---------------------------------------------------------------------------
# TICKET-016: Admin invoice serializer — adds user_email on top of list fields
# ---------------------------------------------------------------------------

class AdminInvoiceListSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id',
            'invoice_number',
            'user_email',
            'plan_name',
            'created_at',
            'billing_period_start',
            'billing_period_end',
            'total_amount',
            'status',
        ]
