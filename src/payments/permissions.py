from django.utils import timezone
from rest_framework.permissions import BasePermission

from .models import Subscription

# Same two "has access" values the extension already checks client-side from
# /api/payments/subscription/ (Dashboard.tsx checkSession) — 'active' paid
# subscription or still-running free 'trial'. Anything else (past_due,
# trial_expired, cancelled, suspended) is not.
ACTIVE_STATUSES = ('active', 'trial')


class HasActiveSubscription(BasePermission):
    """
    Blocks the request unless the user's subscription/trial is currently
    active: status is 'active' or 'trial', AND its end date hasn't passed
    (current_period_end on the paid Subscription row, or trial_end_date on
    User when there's no Subscription row yet — same two-tier lookup
    SubscriptionStatusView uses).

    Previously this was only checked once, client-side, when the extension's
    side panel first opened — an expired subscription didn't stop anything
    for the rest of that browser session (a JWT refresh doesn't re-check it
    either). This enforces it server-side, per request, so listing actions
    stop the moment the subscription is actually invalid.
    """
    message = 'Your trial or subscription has ended. Please complete your payment to continue.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        now = timezone.now()
        try:
            subscription = Subscription.objects.get(user=user)
        except Subscription.DoesNotExist:
            # No paid Subscription row yet — still governed by the free-trial
            # fields tracked directly on User.
            if user.account_status not in ACTIVE_STATUSES:
                return False
            return not user.trial_end_date or user.trial_end_date > now

        if subscription.status not in ACTIVE_STATUSES:
            return False
        return not subscription.current_period_end or subscription.current_period_end > now
