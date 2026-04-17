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
            'is_custom',
            'created_at',
        ]


# ---------------------------------------------------------------------------
# Admin – Custom Plan Management
# ---------------------------------------------------------------------------

class AdminCustomPlanSerializer(serializers.ModelSerializer):
    """Used by admin to create/update custom plans. Stripe IDs are read-only (set after sync)."""
    assigned_user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        help_text='List of user IDs to assign to this custom plan.',
    )
    assigned_users_info = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Plan
        fields = [
            'id',
            'name',
            'price_aud',
            'listing_quota',
            'overage_rate_aud',
            'is_active',
            'is_custom',
            'stripe_price_id',
            'stripe_overage_price_id',
            'assigned_user_ids',
            'assigned_users_info',
            'created_at',
        ]
        read_only_fields = ['id', 'is_custom', 'stripe_price_id', 'stripe_overage_price_id', 'created_at']

    def get_assigned_users_info(self, obj):
        return [
            {'id': u.id, 'email': u.email, 'full_name': f"{u.first_name} {u.last_name}".strip()}
            for u in obj.assigned_users.all()
        ]

    def validate_price_aud(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("price_aud must be greater than 0.")
        return value

    def validate_listing_quota(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("listing_quota must be greater than 0.")
        return value

    def validate_overage_rate_aud(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("overage_rate_aud cannot be negative.")
        return value

    def validate_assigned_user_ids(self, value):
        from accounts.models import User
        if not value:
            return value
        existing_ids = set(User.objects.filter(id__in=value, is_staff=False, is_superuser=False).values_list('id', flat=True))
        invalid = set(value) - existing_ids
        if invalid:
            raise serializers.ValidationError(f"User IDs not found or are admin accounts: {sorted(invalid)}")
        return value

    def create(self, validated_data):
        assigned_user_ids = validated_data.pop('assigned_user_ids', [])
        plan = Plan.objects.create(**validated_data, is_custom=True)
        if assigned_user_ids:
            plan.assigned_users.set(assigned_user_ids)
        return plan

    def update(self, instance, validated_data):
        assigned_user_ids = validated_data.pop('assigned_user_ids', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if assigned_user_ids is not None:
            instance.assigned_users.set(assigned_user_ids)
        return instance


class AdminPlanAssignUsersSerializer(serializers.Serializer):
    """Used by admin to assign/replace user assignments on a custom plan."""
    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text='Full replacement list of user IDs to assign to this plan.',
    )

    def validate_user_ids(self, value):
        from accounts.models import User
        existing_ids = set(User.objects.filter(id__in=value, is_staff=False, is_superuser=False).values_list('id', flat=True))
        invalid = set(value) - existing_ids
        if invalid:
            raise serializers.ValidationError(f"User IDs not found or are admin accounts: {sorted(invalid)}")
        return value


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
