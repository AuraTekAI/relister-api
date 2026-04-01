import logging
import stripe
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from utils.custom_pagination import CustomPageNumberPagination
from .stripe_utils import sync_overage_subscription_item


def _sget(obj, key, default=None):
    """Get a value from a Stripe object or plain dict safely."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
from .models import Plan, Subscription, DiscountCode
from VehicleListing.models import Invoice
from .serializers import (
    PlanSerializer,
    SubscriptionStatusSerializer,
    TrialStatusSerializer,
    UsageSerializer,
    InvoiceListSerializer,
    InvoiceDetailSerializer,
    DiscountCodeSerializer,
    AdminInvoiceListSerializer,
    AdminDiscountCodeSerializer,
)

logger = logging.getLogger('relister_views')


# ---------------------------------------------------------------------------
# Stripe Coupon sync helpers
# ---------------------------------------------------------------------------

def _sync_discount_to_stripe(discount):
    """
    Create a Stripe Coupon matching the given DiscountCode and store its id.
    Uses discount.code as the Stripe coupon id for a stable, predictable mapping.
    Graceful: logs errors without raising so the caller always succeeds locally.
    """
    import math
    stripe.api_key = settings.STRIPE_SECRET_KEY
    coupon_id = discount.code

    kwargs = {
        'id': coupon_id,
        'name': discount.code,
        'duration': 'once',
    }
    if discount.max_uses is not None:
        kwargs['max_redemptions'] = discount.max_uses
    kwargs['redeem_by'] = math.floor(discount.valid_until.timestamp())

    if discount.discount_type == 'percentage':
        kwargs['percent_off'] = float(discount.discount_value)
    else:
        kwargs['amount_off'] = int(discount.discount_value * 100)
        kwargs['currency'] = 'aud'

    try:
        coupon = stripe.Coupon.create(**kwargs)
        discount.stripe_coupon_id = coupon.id
        discount.save(update_fields=['stripe_coupon_id'])
        logger.info(f"_sync_discount_to_stripe: Created Stripe coupon '{coupon.id}' for DiscountCode '{discount.code}'.")
        return coupon.id
    except stripe.error.InvalidRequestError as exc:
        if 'already exists' in str(exc).lower() or 'resource_already_exists' in str(exc).lower():
            try:
                existing = stripe.Coupon.retrieve(coupon_id)
                discount.stripe_coupon_id = existing.id
                discount.save(update_fields=['stripe_coupon_id'])
                logger.info(f"_sync_discount_to_stripe: Coupon '{coupon_id}' already exists in Stripe — linked.")
                return existing.id
            except stripe.error.StripeError as inner_exc:
                logger.error(f"_sync_discount_to_stripe: Failed to retrieve existing coupon '{coupon_id}': {inner_exc}")
                return None
        logger.error(f"_sync_discount_to_stripe: Stripe InvalidRequestError for '{discount.code}': {exc}")
        return None
    except stripe.error.StripeError as exc:
        logger.error(f"_sync_discount_to_stripe: Stripe error for '{discount.code}': {exc}")
        return None


def _archive_stripe_coupon(stripe_coupon_id):
    """
    Delete (archive) a Stripe coupon so it can no longer be redeemed.
    Graceful: ignores 'no such coupon' errors, logs others.
    """
    if not stripe_coupon_id:
        return
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        stripe.Coupon.delete(stripe_coupon_id)
        logger.info(f"_archive_stripe_coupon: Deleted Stripe coupon '{stripe_coupon_id}'.")
    except stripe.error.InvalidRequestError as exc:
        if 'no such coupon' in str(exc).lower():
            logger.info(f"_archive_stripe_coupon: Coupon '{stripe_coupon_id}' already gone from Stripe.")
        else:
            logger.error(f"_archive_stripe_coupon: Failed to delete coupon '{stripe_coupon_id}': {exc}")
    except stripe.error.StripeError as exc:
        logger.error(f"_archive_stripe_coupon: Stripe error deleting '{stripe_coupon_id}': {exc}")


class PlanListView(APIView):
    """GET /api/payments/plans/ — list all active plans."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="List active plans",
        operation_description="Returns all active subscription plans (Starter, Professional, Enterprise).",
        responses={200: PlanSerializer(many=True)},
    )
    def get(self, request):
        plans = Plan.objects.filter(is_active=True).order_by('price_aud')
        serializer = PlanSerializer(plans, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SubscriptionStatusView(APIView):
    """GET /api/payments/subscription/ — current user's subscription or trial status."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get subscription status",
        operation_description="Returns current subscription or trial status for the authenticated user.",
        responses={200: SubscriptionStatusSerializer()},
    )
    def get(self, request):
        user = request.user

        # Try to get the real subscription record first
        try:
            subscription = Subscription.objects.select_related('plan').get(user=user)
            serializer = SubscriptionStatusSerializer(subscription)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Subscription.DoesNotExist:
            pass

        # No Subscription record — derive from User model trial fields
        now = timezone.now()
        days_remaining = None
        if user.trial_end_date:
            delta = user.trial_end_date - now
            days_remaining = max(delta.days, 0)

        data = {
            'status': user.account_status,
            'account_status': user.account_status,
            'days_remaining': days_remaining,
            'trial_end_date': user.trial_end_date,
            'trial_start_date': user.trial_start_date,
            'listing_count': user.daily_listing_count,
            'plan': None,
            'listing_quota': None,
            'overage_rate_aud': None,
        }
        serializer = TrialStatusSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CheckoutView(APIView):
    """POST /api/payments/checkout/ — create a Stripe Checkout session."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Create Stripe Checkout session",
        operation_description="Creates a Stripe Checkout hosted payment page for the given plan. Returns a checkout_url to redirect the user to.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['plan_id'],
            properties={
                'plan_id': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='ID of the plan to subscribe to (from /api/payments/plans/).',
                    example=1,
                ),
            },
        ),
        responses={
            200: openapi.Response(
                description="Checkout session created.",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'checkout_url': openapi.Schema(type=openapi.TYPE_STRING, description='Stripe hosted checkout URL.'),
                    },
                ),
            ),
            400: "plan_id is required or plan is Enterprise (contact sales).",
            404: "Plan not found or inactive.",
            502: "Stripe API error.",
        },
    )
    def post(self, request):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        plan_id = request.data.get('plan_id')

        if not plan_id:
            return Response(
                {'detail': 'plan_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            plan = Plan.objects.get(id=plan_id, is_active=True)
        except Plan.DoesNotExist:
            return Response(
                {'detail': 'Plan not found or inactive.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Enterprise plans have no Stripe price — contact sales only
        if not plan.stripe_price_id:
            return Response(
                {'detail': 'This plan requires contacting sales. No checkout available.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user

        # Retrieve existing Stripe customer ID if present
        stripe_customer_id = None
        existing_sub = None
        try:
            existing_sub = Subscription.objects.get(user=user)
            stripe_customer_id = existing_sub.stripe_customer_id
        except Subscription.DoesNotExist:
            pass

        if not stripe_customer_id:
            try:
                customer = stripe.Customer.create(
                    email=user.email,
                    name=f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email,
                    metadata={'user_id': user.id},
                )
                stripe_customer_id = customer.id
                # Persist the new customer ID so future checkouts reuse it
                if existing_sub:
                    existing_sub.stripe_customer_id = stripe_customer_id
                    existing_sub.save(update_fields=['stripe_customer_id', 'updated_at'])
            except stripe.error.StripeError as exc:
                logger.error(f"Stripe customer creation failed for user {user.id}: {exc}")
                return Response(
                    {'detail': 'Failed to create Stripe customer. Please try again.'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        # Base subscription + metered overage price (usage reported when listing exceeds quota).
        line_items = [{'price': plan.stripe_price_id, 'quantity': 1}]
        if plan.stripe_overage_price_id:
            line_items.append({'price': plan.stripe_overage_price_id})

        # Pass pending discount coupon to checkout if user has one applied.
        # If stripe_coupon_id is missing (e.g. Stripe was down at admin create time),
        # attempt a re-sync now so the user gets their discount at checkout.
        checkout_discounts = []
        if existing_sub and existing_sub.active_discount_code and existing_sub.active_discount_code.is_valid():
            discount = existing_sub.active_discount_code
            if not discount.stripe_coupon_id:
                logger.warning(
                    f"CheckoutView: DiscountCode '{discount.code}' has no stripe_coupon_id — "
                    f"attempting re-sync before checkout for user {user.id}."
                )
                _sync_discount_to_stripe(discount)
                discount.refresh_from_db(fields=['stripe_coupon_id'])
            if discount.stripe_coupon_id:
                checkout_discounts = [{'coupon': discount.stripe_coupon_id}]
                logger.info(
                    f"CheckoutView: Attaching coupon '{discount.stripe_coupon_id}' "
                    f"to checkout session for user {user.id}."
                )
            else:
                logger.warning(
                    f"CheckoutView: Re-sync of DiscountCode '{discount.code}' failed — "
                    f"proceeding without discount for user {user.id}."
                )

        try:
            session_kwargs = dict(
                customer=stripe_customer_id,
                mode='subscription',
                line_items=line_items,
                success_url=settings.STRIPE_SUCCESS_URL,
                cancel_url=settings.STRIPE_CANCEL_URL,
                metadata={
                    'user_id': str(user.id),
                    'plan_id': str(plan.id),
                },
            )
            if checkout_discounts:
                session_kwargs['discounts'] = checkout_discounts
            session = stripe.checkout.Session.create(**session_kwargs)
        except stripe.error.StripeError as exc:
            logger.error(f"Stripe checkout session creation failed for user {user.id}: {exc}")
            return Response(
                {'detail': 'Failed to create checkout session. Please try again.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({'checkout_url': session.url}, status=status.HTTP_200_OK)


@method_decorator(csrf_exempt, name='dispatch')
class WebhookView(APIView):
    """POST /api/payments/webhook/ — Stripe webhook handler."""
    permission_classes = [AllowAny]
    authentication_classes = []  # Skip JWT — Stripe posts without Authorization header

    @swagger_auto_schema(
        operation_summary="Stripe webhook receiver",
        operation_description="Receives and processes Stripe webhook events. Called by Stripe only — not for direct use.",
        responses={200: "Event processed.", 400: "Invalid payload or signature."},
    )
    def post(self, request):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            logger.warning("Stripe webhook received invalid payload.")
            return Response({'detail': 'Invalid payload.'}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.error.SignatureVerificationError:
            logger.warning("Stripe webhook signature verification failed.")
            return Response({'detail': 'Invalid signature.'}, status=status.HTTP_400_BAD_REQUEST)

        event_type = event['type']
        data = event['data']['object']
        logger.info(f"Stripe webhook received: {event_type} (id={event['id']})")

        try:
            if event_type == 'checkout.session.completed':
                self._handle_checkout_completed(data)
            elif event_type == 'customer.subscription.updated':
                self._handle_subscription_updated(data)
            elif event_type == 'customer.subscription.deleted':
                self._handle_subscription_deleted(data)
            elif event_type == 'invoice.payment_failed':
                self._handle_invoice_payment_failed(data)
            elif event_type == 'invoice.payment_succeeded':
                self._handle_invoice_payment_succeeded(data)
        except Exception as exc:
            # Return 200 to prevent Stripe retries — log for investigation
            logger.error(f"Error processing Stripe event {event_type} (id={event['id']}): {exc}", exc_info=True)
            return Response({'detail': 'Event received.'}, status=status.HTTP_200_OK)

        return Response({'detail': 'Webhook processed.'}, status=status.HTTP_200_OK)

    def _handle_checkout_completed(self, session):
        from accounts.models import User

        metadata = _sget(session, 'metadata') or {}
        user_id = _sget(metadata, 'user_id')
        plan_id = _sget(metadata, 'plan_id')
        stripe_customer_id = _sget(session, 'customer')
        stripe_subscription_id = _sget(session, 'subscription')

        if not user_id or not stripe_subscription_id:
            logger.warning(f"checkout.session.completed missing metadata: session={_sget(session, 'id')}")
            return

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            logger.error(f"checkout.session.completed: User {user_id} not found.")
            return

        plan = None
        if plan_id:
            try:
                plan = Plan.objects.get(id=plan_id)
            except Plan.DoesNotExist:
                logger.warning(f"checkout.session.completed: Plan {plan_id} not found.")

        # Retrieve full subscription from Stripe to get billing period dates
        try:
            stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
        except stripe.error.StripeError as exc:
            logger.error(f"checkout.session.completed: Failed to retrieve Stripe subscription {stripe_subscription_id}: {exc}", exc_info=True)
            raise  # re-raise so the outer handler logs it and Stripe retries

        logger.info(f"checkout.session.completed: Retrieved stripe_sub {stripe_subscription_id}, status={_sget(stripe_sub, 'status')}, items={_sget(stripe_sub, 'items')}")

        # Stripe API 2026-02-25.clover moved billing period to items.data[0]
        _items = _sget(stripe_sub, 'items')
        _items_data = getattr(_items, 'data', None) if not isinstance(_items, dict) else _items.get('data')
        sub_item = _items_data[0] if _items_data else None
        if sub_item and _sget(sub_item, 'current_period_start'):
            period_start = datetime.fromtimestamp(_sget(sub_item, 'current_period_start'), tz=dt_timezone.utc)
            period_end = datetime.fromtimestamp(_sget(sub_item, 'current_period_end'), tz=dt_timezone.utc)
        else:
            # Fallback for older API versions
            period_start = datetime.fromtimestamp(_sget(stripe_sub, 'current_period_start'), tz=dt_timezone.utc)
            period_end = datetime.fromtimestamp(_sget(stripe_sub, 'current_period_end'), tz=dt_timezone.utc)

        sub_obj, created = Subscription.objects.update_or_create(
            user=user,
            defaults={
                'plan': plan,
                'stripe_customer_id': stripe_customer_id,
                'stripe_subscription_id': stripe_subscription_id,
                'status': 'active',
                'current_period_start': period_start,
                'current_period_end': period_end,
                'listing_count': 0,
            },
        )

        logger.info(
            f"checkout.session.completed: Subscription {'created' if created else 'updated'} "
            f"(id={sub_obj.id}, stripe_sub={stripe_subscription_id}) for user {user.email}."
        )
        if sub_obj.active_discount_code:
            if sub_obj.active_discount_code.is_valid():
                logger.info(
                    f"checkout.session.completed: active_discount_code='{sub_obj.active_discount_code.code}' "
                    f"preserved on subscription {sub_obj.id} for user {user.email}."
                )
            else:
                # Discount expired/exhausted since it was applied — clear it so it's not misapplied
                logger.warning(
                    f"checkout.session.completed: active_discount_code='{sub_obj.active_discount_code.code}' "
                    f"is no longer valid — clearing from subscription {sub_obj.id} for user {user.email}."
                )
                sub_obj.active_discount_code = None
                sub_obj.save(update_fields=['active_discount_code', 'updated_at'])

        user.account_status = 'active'
        user.listing_count = 0
        user.relist_cycles = 0
        user.overage_count = 0
        user.save(update_fields=['account_status', 'listing_count', 'relist_cycles', 'overage_count', 'updated_at'])
        logger.info(f"checkout.session.completed: User {user.email} activated on plan '{plan}'.")

        if plan:
            sync_overage_subscription_item(sub_obj, stripe_sub, plan)

    def _handle_subscription_updated(self, stripe_sub):
        stripe_subscription_id = _sget(stripe_sub, 'id')
        try:
            subscription = Subscription.objects.select_related('user').get(
                stripe_subscription_id=stripe_subscription_id
            )
        except Subscription.DoesNotExist:
            logger.warning(f"customer.subscription.updated: {stripe_subscription_id} not in DB.")
            return

        _items = _sget(stripe_sub, 'items')
        _items_data = getattr(_items, 'data', None) if not isinstance(_items, dict) else _items.get('data')
        sub_item = _items_data[0] if _items_data else None
        if sub_item and _sget(sub_item, 'current_period_start'):
            new_period_start = datetime.fromtimestamp(_sget(sub_item, 'current_period_start'), tz=dt_timezone.utc)
            new_period_end = datetime.fromtimestamp(_sget(sub_item, 'current_period_end'), tz=dt_timezone.utc)
        else:
            new_period_start = datetime.fromtimestamp(_sget(stripe_sub, 'current_period_start'), tz=dt_timezone.utc)
            new_period_end = datetime.fromtimestamp(_sget(stripe_sub, 'current_period_end'), tz=dt_timezone.utc)

        # Reset listing_count when a new billing period starts
        period_rolled_over = (
            subscription.current_period_start is None
            or subscription.current_period_start != new_period_start
        )

        subscription.current_period_start = new_period_start
        subscription.current_period_end = new_period_end
        subscription.status = self._map_stripe_status(_sget(stripe_sub, 'status'))
        if period_rolled_over:
            subscription.listing_count = 0
            # Reset user counters for the new billing period
            period_user = subscription.user
            period_user.listing_count = 0
            period_user.relist_cycles = 0
            period_user.overage_count = 0
            period_user.save(update_fields=['listing_count', 'relist_cycles', 'overage_count', 'updated_at'])

        # Sync cancel_at_period_end from Stripe — this is authoritative.
        # When the period finally ends, Stripe fires customer.subscription.deleted
        # which sets status=cancelled. Until then, cancel_at_period_end=True means
        # "scheduled to cancel — access still active".
        stripe_cancel_at_period_end = _sget(stripe_sub, 'cancel_at_period_end') or False
        subscription.cancel_at_period_end = stripe_cancel_at_period_end
        # If Stripe has cleared the flag (e.g. admin reactivated), clear cancelled_at too
        if not stripe_cancel_at_period_end:
            subscription.cancelled_at = None

        subscription.save(update_fields=[
            'current_period_start', 'current_period_end', 'status', 'listing_count',
            'cancel_at_period_end', 'cancelled_at', 'updated_at',
        ])
        logger.info(f"customer.subscription.updated: {stripe_subscription_id} — status={subscription.status}, cancel_at_period_end={stripe_cancel_at_period_end}.")

        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            stripe_sub_full = stripe.Subscription.retrieve(
                stripe_subscription_id,
                expand=['items.data.price'],
            )
            sync_overage_subscription_item(subscription, stripe_sub_full, subscription.plan)
        except stripe.error.StripeError as exc:
            logger.warning(f"customer.subscription.updated: overage item sync failed: {exc}")

    def _handle_subscription_deleted(self, stripe_sub):
        stripe_subscription_id = _sget(stripe_sub, 'id')
        try:
            subscription = Subscription.objects.select_related('user').get(
                stripe_subscription_id=stripe_subscription_id
            )
        except Subscription.DoesNotExist:
            logger.warning(f"customer.subscription.deleted: {stripe_subscription_id} not found.")
            return

        subscription.status = 'cancelled'
        subscription.cancelled_at = timezone.now()
        subscription.save(update_fields=['status', 'cancelled_at', 'updated_at'])

        user = subscription.user
        user.account_status = 'trial_expired'
        user.save(update_fields=['account_status'])
        logger.info(f"customer.subscription.deleted: User {user.email} subscription cancelled.")

    def _handle_invoice_payment_failed(self, invoice):
        stripe_subscription_id = _sget(invoice, 'subscription')
        if not stripe_subscription_id:
            return
        try:
            subscription = Subscription.objects.get(
                stripe_subscription_id=stripe_subscription_id
            )
        except Subscription.DoesNotExist:
            logger.warning(f"invoice.payment_failed: {stripe_subscription_id} not found.")
            return

        subscription.status = 'past_due'
        subscription.save(update_fields=['status', 'updated_at'])
        logger.info(f"invoice.payment_failed: {stripe_subscription_id} marked past_due.")

        inv_meta = _sget(invoice, 'metadata') or {}
        if _sget(inv_meta, 'source') == 'listing_overage':
            from .tasks import generate_listing_overage_invoice_from_webhook
            generate_listing_overage_invoice_from_webhook.delay(
                subscription.id,
                _sget(invoice, 'id'),
                _sget(inv_meta, 'vehicle_listing_id'),
                paid=False,
            )
            return

        # Trigger async invoice generation (unpaid)
        from .tasks import generate_invoice
        generate_invoice.delay(subscription.id, stripe_invoice_id=_sget(invoice, 'id'), paid=False)

    def _handle_invoice_payment_succeeded(self, invoice):
        stripe_subscription_id = _sget(invoice, 'subscription')
        stripe_customer_id = _sget(invoice, 'customer')
        logger.info(
            f"invoice.payment_succeeded: processing invoice {_sget(invoice, 'id')} — "
            f"subscription={stripe_subscription_id}, customer={stripe_customer_id}, "
            f"amount={_sget(invoice, 'amount_paid')}, status={_sget(invoice, 'status')}"
        )
        subscription = None

        # Primary lookup by stripe_subscription_id
        if stripe_subscription_id:
            try:
                subscription = Subscription.objects.get(
                    stripe_subscription_id=stripe_subscription_id
                )
            except Subscription.DoesNotExist:
                pass

        # Fallback: Stripe sometimes sends subscription=None on the initial checkout invoice.
        # Look up via stripe_customer_id instead.
        if not subscription and stripe_customer_id:
            try:
                subscription = Subscription.objects.get(
                    stripe_customer_id=stripe_customer_id
                )
                logger.info(
                    f"invoice.payment_succeeded: found subscription via customer {stripe_customer_id} "
                    f"(stripe returned subscription=None on invoice {_sget(invoice, 'id')})."
                )
                # Patch stripe_subscription_id if missing
                if stripe_subscription_id and not subscription.stripe_subscription_id:
                    subscription.stripe_subscription_id = stripe_subscription_id
                    subscription.save(update_fields=['stripe_subscription_id', 'updated_at'])
            except Subscription.DoesNotExist:
                pass

        if not subscription:
            # Still not found — schedule a delayed retry
            logger.warning(
                f"invoice.payment_succeeded: no subscription found for "
                f"stripe_subscription_id={stripe_subscription_id}, customer={stripe_customer_id} — "
                f"scheduling delayed retry."
            )
            from .tasks import generate_invoice_delayed
            generate_invoice_delayed.apply_async(
                kwargs={
                    'stripe_subscription_id': stripe_subscription_id,
                    'stripe_customer_id': stripe_customer_id,
                    'stripe_invoice_id': _sget(invoice, 'id'),
                },
                countdown=15,
            )
            return

        inv_meta = _sget(invoice, 'metadata') or {}
        if _sget(inv_meta, 'source') == 'listing_overage':
            from .tasks import generate_listing_overage_invoice_from_webhook
            generate_listing_overage_invoice_from_webhook.delay(
                subscription.id,
                _sget(invoice, 'id'),
                _sget(inv_meta, 'vehicle_listing_id'),
                paid=True,
            )
            return

        logger.info(f"invoice.payment_succeeded: dispatching generate_invoice for subscription id={subscription.id}.")
        from .tasks import generate_invoice
        generate_invoice.delay(subscription.id, stripe_invoice_id=_sget(invoice, 'id'), paid=True)

    @staticmethod
    def _map_stripe_status(stripe_status):
        return {
            'active': 'active',
            'past_due': 'past_due',
            'canceled': 'cancelled',
            'unpaid': 'past_due',
            'trialing': 'trial',
            'paused': 'suspended',
            'incomplete': 'past_due',
            'incomplete_expired': 'cancelled',
        }.get(stripe_status, 'active')


class BillingPortalView(APIView):
    """GET /api/payments/portal/ — create a Stripe billing portal session."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get Stripe billing portal URL",
        operation_description="Creates a Stripe billing portal session. Returns a portal_url where the user can manage their subscription, update payment method, and view invoices.",
        responses={
            200: openapi.Response(
                description="Portal session created.",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'portal_url': openapi.Schema(type=openapi.TYPE_STRING, description='Stripe billing portal URL.'),
                    },
                ),
            ),
            400: "No Stripe customer ID on record.",
            404: "No subscription found.",
            502: "Stripe API error.",
        },
    )
    def get(self, request):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        user = request.user

        try:
            subscription = Subscription.objects.get(user=user)
        except Subscription.DoesNotExist:
            return Response(
                {'detail': 'No subscription found for this user.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not subscription.stripe_customer_id:
            return Response(
                {'detail': 'No Stripe customer associated with this subscription.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            portal_session = stripe.billing_portal.Session.create(
                customer=subscription.stripe_customer_id,
                return_url=settings.STRIPE_CANCEL_URL,
            )
        except stripe.error.StripeError as exc:
            logger.error(f"Stripe billing portal creation failed for user {user.id}: {exc}")
            return Response(
                {'detail': 'Failed to create billing portal session.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({'portal_url': portal_session.url}, status=status.HTTP_200_OK)


class CancelSubscriptionView(APIView):
    """POST /api/payments/cancel/ — cancel subscription at period end."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Cancel subscription",
        operation_description="Marks the subscription to cancel at the end of the current billing period. The user retains access until then.",
        request_body=openapi.Schema(type=openapi.TYPE_OBJECT, properties={}),
        responses={
            200: "Subscription will be cancelled at period end.",
            400: "Already cancelled or no Stripe subscription ID.",
            404: "No subscription found.",
            502: "Stripe API error.",
        },
    )
    def post(self, request):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        user = request.user

        try:
            subscription = Subscription.objects.select_related('plan').get(user=user)
        except Subscription.DoesNotExist:
            return Response(
                {'detail': 'No subscription found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not subscription.stripe_subscription_id:
            return Response(
                {'detail': 'No Stripe subscription ID associated with this record.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if subscription.status == 'cancelled':
            return Response(
                {'detail': 'Subscription is already cancelled.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if subscription.cancel_at_period_end:
            return Response(
                {'detail': 'Subscription is already scheduled for cancellation at period end.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            stripe.Subscription.modify(
                subscription.stripe_subscription_id,
                cancel_at_period_end=True,
            )
        except stripe.error.StripeError as exc:
            logger.error(f"Stripe subscription cancel failed for user {user.id}: {exc}")
            return Response(
                {'detail': 'Failed to cancel subscription. Please try again.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        subscription.cancel_at_period_end = True
        subscription.cancelled_at = timezone.now()
        subscription.save(update_fields=['cancel_at_period_end', 'cancelled_at', 'updated_at'])

        logger.info(f"CancelSubscription: User {user.email} scheduled cancellation at period end ({subscription.current_period_end}).")

        serializer = SubscriptionStatusSerializer(subscription)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# TICKET-011: Usage Tracker
# ---------------------------------------------------------------------------

class UsageTrackerView(APIView):
    """GET /api/payments/usage/ — real-time usage data for the current billing period."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get usage tracker data",
        operation_description=(
            "Returns active listing count, relist cycles, quota usage, and overage "
            "for the current billing period. Works for both trial and paid users."
        ),
        responses={200: UsageSerializer()},
    )
    def get(self, request):
        from VehicleListing.models import VehicleListing as VL

        user = request.user

        active_listing_count = VL.objects.filter(user=user, status='completed').count()

        try:
            subscription = Subscription.objects.select_related('plan').get(user=user)
            period_start = subscription.current_period_start
            period_end = subscription.current_period_end
            listings_used = user.listing_count
            relist_cycles_this_month = user.relist_cycles
            listing_quota = subscription.plan.listing_quota if subscription.plan else None
            overage_rate = subscription.plan.overage_rate_aud if subscription.plan else None
            overage_count = user.overage_count

        except Subscription.DoesNotExist:
            # Trial user
            period_start = user.trial_start_date
            period_end = user.trial_end_date
            listings_used = user.listing_count
            relist_cycles_this_month = user.relist_cycles
            listing_quota = None
            overage_rate = None
            overage_count = 0

        if listing_quota and listing_quota > 0:
            usage_percentage = round(min((listings_used / listing_quota) * 100, 100), 1)
        else:
            usage_percentage = None

        overage_amount = (
            round(overage_count * float(overage_rate), 2)
            if overage_rate and overage_count > 0
            else 0.00
        )

        data = {
            'active_listing_count': active_listing_count,
            'relist_cycles_this_month': relist_cycles_this_month,
            'listings_used': listings_used,
            'listing_quota': listing_quota,
            'usage_percentage': usage_percentage,
            'overage_count': overage_count,
            'overage_rate': overage_rate,
            'overage_amount': overage_amount,
            'period_start': period_start,
            'period_end': period_end,
        }
        serializer = UsageSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# TICKET-012: Invoice Management
# ---------------------------------------------------------------------------

class InvoiceListView(APIView):
    """GET /api/payments/invoices/ — paginated list of user's invoices."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="List invoices",
        operation_description="Returns a paginated list of all invoices for the authenticated user.",
        manual_parameters=[
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Page number'),
            openapi.Parameter('limit', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Items per page (default: 10, max: 100)'),
        ],
        responses={200: openapi.Response(
            description="Paginated invoice list",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'count': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'next': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
                    'previous': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
                    'results': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)),
                },
            ),
        )},
    )
    def get(self, request):
        invoices = Invoice.objects.filter(user=request.user).order_by('-created_at')
        paginator = CustomPageNumberPagination()
        page = paginator.paginate_queryset(invoices, request)
        serializer = InvoiceListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class InvoiceDetailView(APIView):
    """GET /api/payments/invoices/<pk>/ — full invoice detail."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get invoice detail",
        operation_description="Returns the full itemised breakdown for a single invoice.",
        responses={200: InvoiceDetailSerializer(), 404: "Invoice not found."},
    )
    def get(self, request, pk):
        try:
            invoice = Invoice.objects.select_related('discount_code').get(
                pk=pk, user=request.user
            )
        except Invoice.DoesNotExist:
            return Response({'detail': 'Invoice not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = InvoiceDetailSerializer(invoice)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ApplyDiscountView(APIView):
    """POST /api/payments/apply-discount/ — validate and apply a discount code."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Apply discount code",
        operation_description=(
            "Validates a discount code and attaches it to the user's active subscription. "
            "The discount will be applied on the next generated invoice."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['code'],
            properties={
                'code': openapi.Schema(type=openapi.TYPE_STRING, description='Discount code string.'),
            },
        ),
        responses={
            200: openapi.Response(
                description="Discount code is valid and applied.",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'valid': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                        'discount_type': openapi.Schema(type=openapi.TYPE_STRING),
                        'discount_value': openapi.Schema(type=openapi.TYPE_STRING),
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'stripe_applied': openapi.Schema(
                            type=openapi.TYPE_BOOLEAN,
                            description='True if the coupon was also applied to the Stripe subscription immediately. False for trial users (applied at checkout) or if Stripe was unreachable.',
                        ),
                    },
                ),
            ),
            400: "Missing code, invalid, expired, exhausted, or already applied.",
            404: "No active subscription found.",
        },
    )
    def post(self, request):
        code_str = request.data.get('code', '').strip().upper()
        if not code_str:
            return Response({'detail': 'code is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            discount = DiscountCode.objects.get(code=code_str)
        except DiscountCode.DoesNotExist:
            return Response({'detail': 'Invalid discount code.'}, status=status.HTTP_400_BAD_REQUEST)

        if not discount.is_valid():
            return Response(
                {'detail': 'This discount code is expired, inactive, or has reached its usage limit.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            subscription = Subscription.objects.select_related('active_discount_code').get(
                user=request.user,
                status__in=['active', 'past_due', 'trial'],
            )
        except Subscription.DoesNotExist:
            return Response(
                {'detail': 'No active subscription found. Subscribe to a plan first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Idempotency: prevent applying the same code twice
        if subscription.active_discount_code_id == discount.pk:
            return Response(
                {'detail': f"Discount code '{discount.code}' is already applied to your subscription."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription.active_discount_code = discount
        subscription.save(update_fields=['active_discount_code', 'updated_at'])

        stripe_applied = False
        if subscription.stripe_subscription_id and discount.stripe_coupon_id:
            try:
                stripe.api_key = settings.STRIPE_SECRET_KEY
                stripe.Subscription.modify(
                    subscription.stripe_subscription_id,
                    discounts=[{'coupon': discount.stripe_coupon_id}],
                )
                stripe_applied = True
                logger.info(
                    f"ApplyDiscount: Applied Stripe coupon '{discount.stripe_coupon_id}' "
                    f"to subscription '{subscription.stripe_subscription_id}' for user {request.user.email}."
                )
            except stripe.error.StripeError as exc:
                logger.error(
                    f"ApplyDiscount: Failed to apply Stripe coupon '{discount.stripe_coupon_id}' "
                    f"to subscription '{subscription.stripe_subscription_id}': {exc}"
                )
        elif subscription.stripe_subscription_id and not discount.stripe_coupon_id:
            logger.warning(
                f"ApplyDiscount: DiscountCode '{discount.code}' has no stripe_coupon_id — "
                f"Stripe subscription not updated. Local discount recorded only."
            )

        if discount.discount_type == 'percentage':
            message = f"Discount applied: {discount.discount_value}% off your next invoice."
        else:
            message = f"Discount applied: ${discount.discount_value} AUD off your next invoice."

        return Response(
            {
                'valid': True,
                'discount_type': discount.discount_type,
                'discount_value': str(discount.discount_value),
                'message': message,
                'stripe_applied': stripe_applied,
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# TICKET-016: Admin – Invoice & Billing Overview
# ---------------------------------------------------------------------------

class AdminInvoiceListView(APIView):
    """GET /api/payments/admin/invoices/ — all invoices across all users (admin only)."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @swagger_auto_schema(
        operation_summary="[Admin] List all invoices",
        operation_description=(
            "Returns all invoices across all users. "
            "Supports filtering by: status (paid/unpaid/overdue), plan_name, date_from (YYYY-MM-DD), date_to (YYYY-MM-DD)."
        ),
        manual_parameters=[
            openapi.Parameter('status', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='paid | unpaid | overdue'),
            openapi.Parameter('plan_name', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='Filter by plan name (case-insensitive)'),
            openapi.Parameter('date_from', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='Invoice created_at >= date (YYYY-MM-DD)'),
            openapi.Parameter('date_to', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='Invoice created_at <= date (YYYY-MM-DD)'),
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Page number'),
            openapi.Parameter('limit', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Items per page (default: 10, max: 100)'),
        ],
        responses={200: openapi.Response(
            description="Paginated invoice list",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'count': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'next': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
                    'previous': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
                    'results': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)),
                },
            ),
        )},
    )
    def get(self, request):
        from VehicleListing.models import Invoice
        from django.utils.dateparse import parse_date

        qs = Invoice.objects.select_related('user').order_by('-created_at')

        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        plan_name = request.query_params.get('plan_name')
        if plan_name:
            qs = qs.filter(plan_name__icontains=plan_name)

        date_from = request.query_params.get('date_from')
        if date_from:
            parsed = parse_date(date_from)
            if parsed:
                qs = qs.filter(created_at__date__gte=parsed)

        date_to = request.query_params.get('date_to')
        if date_to:
            parsed = parse_date(date_to)
            if parsed:
                qs = qs.filter(created_at__date__lte=parsed)

        paginator = CustomPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = AdminInvoiceListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminInvoiceDetailView(APIView):
    """GET /api/payments/admin/invoices/<pk>/ — full invoice detail (admin only, no ownership gate)."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @swagger_auto_schema(
        operation_summary="[Admin] Get invoice detail",
        operation_description="Returns the full itemised breakdown for any invoice. No ownership restriction.",
        responses={200: InvoiceDetailSerializer(), 404: "Invoice not found."},
    )
    def get(self, request, pk):
        from VehicleListing.models import Invoice

        try:
            invoice = Invoice.objects.select_related('discount_code', 'user').get(pk=pk)
        except Invoice.DoesNotExist:
            return Response({'detail': 'Invoice not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = InvoiceDetailSerializer(invoice)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminMarkInvoicePaidView(APIView):
    """POST /api/payments/admin/invoices/<pk>/mark-paid/ — manually mark an invoice as paid."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @swagger_auto_schema(
        operation_summary="[Admin] Mark invoice as paid",
        operation_description="Manually sets an invoice status to 'paid'. Used for edge cases where Stripe did not confirm automatically.",
        responses={
            200: openapi.Response(description="Invoice marked as paid."),
            400: "Invoice is already paid.",
            404: "Invoice not found.",
        },
    )
    def post(self, request, pk):
        from VehicleListing.models import Invoice

        try:
            invoice = Invoice.objects.select_related('user').get(pk=pk)
        except Invoice.DoesNotExist:
            return Response({'detail': 'Invoice not found.'}, status=status.HTTP_404_NOT_FOUND)

        if invoice.status == 'paid':
            return Response({'detail': 'Invoice is already marked as paid.'}, status=status.HTTP_400_BAD_REQUEST)

        invoice.status = 'paid'
        invoice.save(update_fields=['status', 'updated_at'])

        logger.info(f"AdminMarkInvoicePaid: Invoice {invoice.invoice_number} marked paid by admin {request.user.email}.")
        serializer = InvoiceDetailSerializer(invoice)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Admin – Invoice Stats (dashboard cards)
# ---------------------------------------------------------------------------

class AdminInvoiceStatsView(APIView):
    """GET /api/payments/admin/invoices/stats/ — summary counts and amounts for the invoice dashboard cards."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @swagger_auto_schema(
        operation_summary="[Admin] Invoice dashboard stats",
        operation_description=(
            "Returns aggregated invoice statistics for the admin dashboard cards: "
            "total invoices, total revenue collected, outstanding amount, overdue count, current month revenue, "
            "and user counts (total, active, approved, unapproved)."
        ),
        responses={
            200: openapi.Response(
                description="Invoice and user stats",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'total_invoices': openapi.Schema(type=openapi.TYPE_INTEGER, description='All invoices count'),
                        'paid_invoices': openapi.Schema(type=openapi.TYPE_INTEGER, description='Paid invoices count'),
                        'unpaid_invoices': openapi.Schema(type=openapi.TYPE_INTEGER, description='Unpaid invoices count'),
                        'overdue_invoices': openapi.Schema(type=openapi.TYPE_INTEGER, description='Overdue invoices count'),
                        'total_revenue': openapi.Schema(type=openapi.TYPE_STRING, description='Sum of all paid invoice totals (AUD)'),
                        'outstanding_amount': openapi.Schema(type=openapi.TYPE_STRING, description='Sum of unpaid + overdue invoice totals (AUD)'),
                        'overdue_amount': openapi.Schema(type=openapi.TYPE_STRING, description='Sum of overdue invoice totals (AUD)'),
                        'current_month_revenue': openapi.Schema(type=openapi.TYPE_STRING, description='Paid invoice totals for the current calendar month (AUD)'),
                        'total_users': openapi.Schema(type=openapi.TYPE_INTEGER, description='Total registered users (excluding admins)'),
                        'total_active_users': openapi.Schema(type=openapi.TYPE_INTEGER, description='Users with is_active=True (excluding admins)'),
                        'total_approved_users': openapi.Schema(type=openapi.TYPE_INTEGER, description='Users with is_approved=True (excluding admins)'),
                        'total_unapproved_users': openapi.Schema(type=openapi.TYPE_INTEGER, description='Users with is_approved=False (excluding admins)'),
                    },
                ),
            )
        },
    )
    def get(self, request):
        from VehicleListing.models import Invoice
        from accounts.models import User
        from django.db.models import Count, Sum, Q
        from django.utils import timezone

        now = timezone.now()

        agg = Invoice.objects.aggregate(
            total_invoices=Count('id'),
            paid_invoices=Count('id', filter=Q(status='paid')),
            unpaid_invoices=Count('id', filter=Q(status='unpaid')),
            overdue_invoices=Count('id', filter=Q(status='overdue')),
            total_revenue=Sum('total_amount', filter=Q(status='paid')),
            outstanding_amount=Sum('total_amount', filter=Q(status__in=['unpaid', 'overdue'])),
            overdue_amount=Sum('total_amount', filter=Q(status='overdue')),
            current_month_revenue=Sum(
                'total_amount',
                filter=Q(status='paid', created_at__year=now.year, created_at__month=now.month),
            ),
        )

        non_admin_users = User.objects.filter(is_staff=False, is_superuser=False)
        user_agg = non_admin_users.aggregate(
            total_users=Count('id'),
            total_active_users=Count('id', filter=Q(is_active=True)),
            total_approved_users=Count('id', filter=Q(is_approved=True)),
            total_unapproved_users=Count('id', filter=Q(is_approved=False)),
        )

        return Response({
            'total_invoices': agg['total_invoices'] or 0,
            'paid_invoices': agg['paid_invoices'] or 0,
            'unpaid_invoices': agg['unpaid_invoices'] or 0,
            'overdue_invoices': agg['overdue_invoices'] or 0,
            'total_revenue': str(agg['total_revenue'] or '0.00'),
            'outstanding_amount': str(agg['outstanding_amount'] or '0.00'),
            'overdue_amount': str(agg['overdue_amount'] or '0.00'),
            'current_month_revenue': str(agg['current_month_revenue'] or '0.00'),
            'total_users': user_agg['total_users'] or 0,
            'total_active_users': user_agg['total_active_users'] or 0,
            'total_approved_users': user_agg['total_approved_users'] or 0,
            'total_unapproved_users': user_agg['total_unapproved_users'] or 0,
        }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# TICKET-018: Admin – Discount Code Management
# ---------------------------------------------------------------------------

class AdminDiscountCodeListCreateView(APIView):
    """
    GET  /api/payments/admin/discount-codes/       — list all discount codes.
    POST /api/payments/admin/discount-codes/       — create a new discount code.
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    @swagger_auto_schema(
        operation_summary="[Admin] List discount codes",
        operation_description="Returns all discount codes. Supports filtering by is_active (true/false) and discount_type (percentage/fixed).",
        manual_parameters=[
            openapi.Parameter('is_active', openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, description='Filter by active status'),
            openapi.Parameter('discount_type', openapi.IN_QUERY, type=openapi.TYPE_STRING, description='percentage | fixed'),
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Page number'),
            openapi.Parameter('limit', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description='Items per page (default: 10, max: 100)'),
        ],
        responses={200: openapi.Response(
            description="Paginated discount code list",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'count': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'next': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
                    'previous': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
                    'results': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)),
                },
            ),
        )},
    )
    def get(self, request):
        from .models import DiscountCode
        qs = DiscountCode.objects.all().order_by('-created_at')

        is_active = request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')

        discount_type = request.query_params.get('discount_type')
        if discount_type:
            qs = qs.filter(discount_type=discount_type)

        paginator = CustomPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = AdminDiscountCodeSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @swagger_auto_schema(
        operation_summary="[Admin] Create discount code",
        operation_description="Creates a new discount code.",
        request_body=AdminDiscountCodeSerializer,
        responses={
            201: AdminDiscountCodeSerializer(),
            400: "Validation error.",
        },
    )
    def post(self, request):
        serializer = AdminDiscountCodeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        discount = serializer.save()
        logger.info(f"AdminDiscountCode: Created code '{discount.code}' by admin {request.user.email}.")
        _sync_discount_to_stripe(discount)
        return Response(AdminDiscountCodeSerializer(discount).data, status=status.HTTP_201_CREATED)


class AdminDiscountCodeDetailView(APIView):
    """
    GET    /api/payments/admin/discount-codes/<pk>/ — retrieve a discount code.
    PATCH  /api/payments/admin/discount-codes/<pk>/ — update a discount code.
    DELETE /api/payments/admin/discount-codes/<pk>/ — delete a discount code.
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    def _get_object(self, pk):
        from .models import DiscountCode
        try:
            return DiscountCode.objects.get(pk=pk)
        except DiscountCode.DoesNotExist:
            return None

    @swagger_auto_schema(
        operation_summary="[Admin] Get discount code detail",
        responses={200: AdminDiscountCodeSerializer(), 404: "Not found."},
    )
    def get(self, request, pk):
        discount = self._get_object(pk)
        if not discount:
            return Response({'detail': 'Discount code not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(AdminDiscountCodeSerializer(discount).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_summary="[Admin] Update discount code",
        request_body=AdminDiscountCodeSerializer,
        responses={200: AdminDiscountCodeSerializer(), 400: "Validation error.", 404: "Not found."},
    )
    def patch(self, request, pk):
        discount = self._get_object(pk)
        if not discount:
            return Response({'detail': 'Discount code not found.'}, status=status.HTTP_404_NOT_FOUND)

        old_stripe_coupon_id = discount.stripe_coupon_id
        old_discount_type = discount.discount_type
        old_discount_value = discount.discount_value
        old_is_active = discount.is_active

        serializer = AdminDiscountCodeSerializer(discount, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        discount = serializer.save()
        logger.info(f"AdminDiscountCode: Updated code '{discount.code}' by admin {request.user.email}.")

        structural_change = (
            discount.discount_type != old_discount_type
            or discount.discount_value != old_discount_value
        )

        if not discount.is_active and old_is_active:
            # Deactivated — archive the Stripe coupon
            _archive_stripe_coupon(old_stripe_coupon_id)
            discount.stripe_coupon_id = None
            discount.save(update_fields=['stripe_coupon_id'])
        elif discount.is_active and structural_change:
            # Stripe coupons are immutable — delete old and recreate
            _archive_stripe_coupon(old_stripe_coupon_id)
            discount.stripe_coupon_id = None
            discount.save(update_fields=['stripe_coupon_id'])
            _sync_discount_to_stripe(discount)
        elif discount.is_active and not discount.stripe_coupon_id:
            # Missing coupon (e.g. previous Stripe failure) — create now
            _sync_discount_to_stripe(discount)

        return Response(AdminDiscountCodeSerializer(discount).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_summary="[Admin] Delete discount code",
        responses={204: "Deleted.", 404: "Not found."},
    )
    def delete(self, request, pk):
        discount = self._get_object(pk)
        if not discount:
            return Response({'detail': 'Discount code not found.'}, status=status.HTTP_404_NOT_FOUND)
        code = discount.code
        stripe_coupon_id = discount.stripe_coupon_id
        discount.delete()
        logger.info(f"AdminDiscountCode: Deleted code '{code}' by admin {request.user.email}.")
        _archive_stripe_coupon(stripe_coupon_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
