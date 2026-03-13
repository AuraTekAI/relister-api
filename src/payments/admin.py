from django.contrib import admin
from .models import Plan, Subscription
# Invoice is registered in VehicleListing/admin.py — do not re-register here.


class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price_aud', 'listing_quota', 'overage_rate_aud', 'is_active', 'created_at')
    search_fields = ('name',)
    list_filter = ('is_active',)


class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'plan', 'status', 'listing_count',
        'current_period_start', 'current_period_end',
        'stripe_customer_id', 'stripe_subscription_id',
        'cancelled_at', 'created_at', 'updated_at',
    )
    search_fields = ('user__email', 'stripe_customer_id', 'stripe_subscription_id')
    list_filter = ('status', 'plan')
    raw_id_fields = ('user', 'plan')
    readonly_fields = ('created_at', 'updated_at')


admin.site.register(Plan, PlanAdmin)
admin.site.register(Subscription, SubscriptionAdmin)
