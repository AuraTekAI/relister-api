import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from celery import shared_task
from django.conf import settings
from django.db.models import F
from django.utils import timezone

import stripe

logger = logging.getLogger('relister_views')


def _next_invoice_number():
    """Generate the next sequential invoice number: INV-YYYY-NNNN."""
    from VehicleListing.models import Invoice
    year = timezone.now().year
    prefix = f"INV-{year}-"
    last = (
        Invoice.objects
        .filter(invoice_number__startswith=prefix)
        .order_by('-invoice_number')
        .values_list('invoice_number', flat=True)
        .first()
    )
    if last:
        try:
            seq = int(last.split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


@shared_task(bind=True, queue='scheduling_queue')
def generate_invoice(self, subscription_id, stripe_invoice_id=None, paid=True):
    """
    Generate an Invoice record for a completed billing period.
    Triggered by Stripe webhooks: invoice.payment_succeeded / invoice.payment_failed.
    """
    from .models import Subscription
    from VehicleListing.models import Invoice

    try:
        subscription = Subscription.objects.select_related('plan', 'user', 'active_discount_code').get(
            id=subscription_id
        )
    except Subscription.DoesNotExist:
        logger.error(f"generate_invoice: Subscription {subscription_id} not found.")
        return

    plan = subscription.plan
    user = subscription.user

    if not plan:
        logger.warning(f"generate_invoice: Subscription {subscription_id} has no plan — skipping.")
        return

    # Skip duplicate — same Stripe invoice already recorded
    if stripe_invoice_id and Invoice.objects.filter(stripe_invoice_id=stripe_invoice_id).exists():
        logger.info(f"generate_invoice: Invoice for stripe_invoice_id={stripe_invoice_id} already exists — skipping.")
        return

    # --- Line item calculations ---
    base_charge = plan.price_aud or Decimal('0.00')
    listing_quota = plan.listing_quota or 0
    overage_rate = plan.overage_rate_aud or Decimal('0.00')
    # Safe-mode guard: subscription-cycle invoices are plan-only.
    # Overage is billed ONLY via source=listing_overage webhook path.
    listings_used = subscription.listing_count
    overage_count = 0
    overage_charge = Decimal('0.00')

    # --- Discount ---
    discount_obj = subscription.active_discount_code
    discount_amount = Decimal('0.00')
    if discount_obj:
        if not discount_obj.is_valid():
            # Code expired/exhausted since it was applied — clear it without applying
            logger.warning(
                f"generate_invoice: DiscountCode '{discount_obj.code}' is no longer valid "
                f"(expired, inactive, or exhausted) — skipping discount for subscription {subscription_id}."
            )
            subscription.active_discount_code = None
            subscription.save(update_fields=['active_discount_code', 'updated_at'])
            discount_obj = None
        else:
            pre_discount = base_charge + overage_charge
            if discount_obj.discount_type == 'percentage':
                pct = min(discount_obj.discount_value, Decimal('100'))  # cap at 100%
                discount_amount = (pre_discount * pct / Decimal('100')).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
            else:
                discount_amount = min(discount_obj.discount_value, pre_discount)
            # Increment usage count atomically to prevent race conditions
            from .models import DiscountCode as _DiscountCode
            _DiscountCode.objects.filter(pk=discount_obj.pk).update(used_count=F('used_count') + 1)
            # Clear applied discount so it's not double-applied next cycle
            subscription.active_discount_code = None
            subscription.save(update_fields=['active_discount_code', 'updated_at'])

    subtotal = (base_charge + overage_charge - discount_amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    gst_amount = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total_amount = subtotal + gst_amount

    invoice_status = 'paid' if paid else 'unpaid'

    invoice = Invoice.objects.create(
        invoice_number=_next_invoice_number(),
        user=user,
        subscription=subscription,
        billing_period_start=subscription.current_period_start or timezone.now(),
        billing_period_end=subscription.current_period_end or timezone.now(),
        plan_name=plan.name,
        base_plan_charge=base_charge,
        included_listings=listing_quota,
        relist_cycles=4,  # TODO: Replace with real relist cycle count when relisting mechanism is implemented.
        overage_listings=overage_count,
        overage_rate=overage_rate,
        overage_charge=overage_charge,
        discount_code=discount_obj if discount_obj else None,
        discount_code_str=discount_obj.code if discount_obj else '',
        discount_amount=discount_amount,
        subtotal=subtotal,
        gst_amount=gst_amount,
        total_amount=total_amount,
        status=invoice_status,
        stripe_invoice_id=stripe_invoice_id,
    )

    logger.info(
        f"generate_invoice: Created {invoice.invoice_number} for user {user.email} "
        f"— total={total_amount} status={invoice_status}."
    )

    # Send email notification
    if paid:
        _send_invoice_email(user, invoice)
    else:
        _send_payment_failed_email(user, invoice)

    return invoice.invoice_number


def _send_invoice_email(user, invoice):
    """Send invoice confirmation email if the user has billing reminders enabled."""
    try:
        prefs = user.notification_preferences
        if not prefs.email_billing_reminder:
            return
    except Exception:
        pass  # NotificationPreference may not exist yet — send anyway

    try:
        from django.core.mail import EmailMessage
        from django.template.loader import render_to_string

        subject = f"Invoice {invoice.invoice_number} — Payment Confirmed"
        body = render_to_string('payments/invoice_email.html', {
            'user': user,
            'invoice': invoice,
        })
        email = EmailMessage(subject, body, settings.EMAIL_HOST_USER, [user.email])
        email.content_subtype = 'html'
        email.send(fail_silently=True)
    except Exception as exc:
        logger.error(f"_send_invoice_email: Failed to send invoice email to {user.email}: {exc}")


def _send_payment_failed_email(user, invoice):
    """Send a payment failed alert — always sent regardless of notification preferences."""
    try:
        from django.core.mail import EmailMessage
        from django.template.loader import render_to_string

        subject = f"Action Required: Payment Failed for Invoice {invoice.invoice_number}"
        body = render_to_string('payments/payment_failed_email.html', {
            'user': user,
            'invoice': invoice,
        })
        email = EmailMessage(subject, body, settings.EMAIL_HOST_USER, [user.email])
        email.content_subtype = 'html'
        email.send(fail_silently=True)
    except Exception as exc:
        logger.error(f"_send_payment_failed_email: Failed to send payment failed email to {user.email}: {exc}")


@shared_task(bind=True, queue='scheduling_queue', max_retries=3, default_retry_delay=15)
def generate_invoice_delayed(self, stripe_subscription_id=None, stripe_customer_id=None, stripe_invoice_id=None):
    """
    Delayed retry for generate_invoice when invoice.payment_succeeded arrives before
    checkout.session.completed has written the Subscription row (race condition on first checkout).
    Retries up to 3 times with a 15-second delay between attempts.
    """
    from .models import Subscription

    subscription = None

    if stripe_subscription_id:
        try:
            subscription = Subscription.objects.get(stripe_subscription_id=stripe_subscription_id)
        except Subscription.DoesNotExist:
            pass

    if not subscription and stripe_customer_id:
        try:
            subscription = Subscription.objects.get(stripe_customer_id=stripe_customer_id)
        except Subscription.DoesNotExist:
            pass

    if not subscription:
        logger.warning(
            f"generate_invoice_delayed: Subscription still not found "
            f"(stripe_sub={stripe_subscription_id}, customer={stripe_customer_id}, "
            f"attempt {self.request.retries + 1}/3) — retrying."
        )
        raise self.retry()

    logger.info(f"generate_invoice_delayed: Subscription id={subscription.id} found — generating invoice.")
    generate_invoice.delay(subscription.id, stripe_invoice_id=stripe_invoice_id, paid=True)


def _send_subscription_renewal_notification(user, subscription, days_remaining):
    """
    Send subscription renewal warning email.
    days_remaining: 14, 7, or 1
    """
    logger.info(f"Sending subscription renewal ({days_remaining}d) warning email to {user.email}")
    try:
        from django.core.mail import EmailMessage
        from django.template.loader import render_to_string

        period_end = subscription.current_period_end.strftime('%d %B %Y') if subscription.current_period_end else 'N/A'
        plan_name = subscription.plan.name if subscription.plan else 'N/A'
        context = {
            'user_name': user.first_name or getattr(user, 'contact_person_name', None) or user.email,
            'dealership_name': getattr(user, 'dealership_name', None) or 'N/A',
            'plan_name': plan_name,
            'renewal_date': period_end,
            'days_remaining': days_remaining,
        }

        if days_remaining == 14:
            template = 'payments/subscription_renewal_14days.html'
            subject = 'Your Relister subscription renews in 14 days'
        elif days_remaining == 7:
            template = 'payments/subscription_renewal_7days.html'
            subject = 'Your Relister subscription renews in 7 days'
        else:
            template = 'payments/subscription_renewal_1day.html'
            subject = 'Your Relister subscription renews tomorrow'

        body = render_to_string(template, context)
        email = EmailMessage(subject, body, settings.EMAIL_HOST_USER, [user.email])
        email.content_subtype = 'html'
        email.send(fail_silently=True)
        logger.info(f"Subscription renewal ({days_remaining}d) email sent to {user.email}")
    except Exception as exc:
        logger.error(f"_send_subscription_renewal_notification: Failed to send to {user.email}: {exc}")


@shared_task(queue='scheduling_queue')
def check_subscription_renewal_task():
    """
    Daily task that sends renewal warning emails to active subscribers at 14, 7, and 1 day
    before their current_period_end.
    Runs every day at 00:10 UTC via Celery Beat.
    """
    from .models import Subscription

    now = timezone.now()
    today = now.date()

    logger.info("Starting daily subscription renewal check")

    warning_days = [14, 7, 1]
    total_sent = 0

    for days in warning_days:
        target_date = today + timedelta(days=days)
        subs = Subscription.objects.select_related('user', 'plan').filter(
            status='active',
            current_period_end__date=target_date,
        )
        for sub in subs:
            try:
                prefs = sub.user.notification_preferences
                if not prefs.email_billing_reminder:
                    continue
            except Exception:
                pass  # NotificationPreference may not exist — send anyway
            _send_subscription_renewal_notification(sub.user, sub, days)
            total_sent += 1

    logger.info(f"Subscription renewal check complete. Emails sent: {total_sent}")


@shared_task(bind=True, queue='scheduling_queue')
def mark_overdue_invoices(self):
    """
    Periodic task — runs daily (django-celery-beat).

    1) Reconcile with Stripe: local invoices with a stripe_invoice_id that are still
       unpaid/overdue are refreshed; if Stripe shows paid, local status becomes paid.
    2) Still-unpaid invoices more than 7 days after billing_period_end become overdue.
       One reminder email is sent per invoice at that transition (not on later runs).
    """
    from VehicleListing.models import Invoice
    from payments.stripe_utils import is_stripe_invoice_paid

    stripe.api_key = settings.STRIPE_SECRET_KEY

    synced_paid = 0

    reconcile_qs = (
        Invoice.objects.filter(status__in=['unpaid', 'overdue'])
        .exclude(stripe_invoice_id__isnull=True)
        .exclude(stripe_invoice_id='')
    )

    for inv in reconcile_qs.iterator(chunk_size=50):
        sid = (inv.stripe_invoice_id or '').strip()
        if not sid:
            continue
        try:
            si = stripe.Invoice.retrieve(sid)
            if is_stripe_invoice_paid(si) and inv.status != 'paid':
                inv.status = 'paid'
                inv.save(update_fields=['status', 'updated_at'])
                synced_paid += 1
        except stripe.error.InvalidRequestError as exc:
            err = str(exc).lower()
            if 'no such invoice' in err or 'resource_missing' in err:
                logger.warning(
                    f"mark_overdue_invoices: Stripe invoice missing {sid} (local {inv.invoice_number})"
                )
            else:
                logger.error(f"mark_overdue_invoices: Stripe InvalidRequestError for {sid}: {exc}")
        except stripe.error.StripeError as exc:
            logger.error(f"mark_overdue_invoices: Stripe error retrieving {sid}: {exc}")

    cutoff = timezone.now() - timedelta(days=7)
    overdue_candidates = Invoice.objects.filter(
        status='unpaid',
        billing_period_end__lt=cutoff,
    ).select_related('user')

    marked = 0
    emailed = 0

    for inv in overdue_candidates:
        sid = (inv.stripe_invoice_id or '').strip()
        if sid:
            try:
                si = stripe.Invoice.retrieve(sid)
                if is_stripe_invoice_paid(si):
                    inv.status = 'paid'
                    inv.save(update_fields=['status', 'updated_at'])
                    synced_paid += 1
                    continue
            except stripe.error.StripeError as exc:
                logger.warning(
                    f"mark_overdue_invoices: could not verify Stripe invoice {sid} before overdue: {exc}"
                )

        inv.status = 'overdue'
        inv.save(update_fields=['status', 'updated_at'])
        marked += 1
        if _send_overdue_invoice_reminder(inv.user, inv):
            emailed += 1

    logger.info(
        f"mark_overdue_invoices: synced_paid_from_stripe={synced_paid}, marked_overdue={marked}, "
        f"reminder_emails_sent={emailed}, cutoff={cutoff.date()}."
    )


def _send_overdue_invoice_reminder(user, invoice):
    """
    Send overdue reminder if the user has billing reminder emails enabled.
    Returns True if an email was sent.
    """
    try:
        prefs = user.notification_preferences
        if not prefs.email_billing_reminder:
            return False
    except Exception:
        pass

    try:
        from django.core.mail import EmailMessage
        from django.template.loader import render_to_string

        subject = f"Invoice {invoice.invoice_number} is overdue — payment required"
        body = render_to_string('payments/overdue_invoice_reminder.html', {
            'user': user,
            'invoice': invoice,
        })
        email = EmailMessage(subject, body, settings.EMAIL_HOST_USER, [user.email])
        email.content_subtype = 'html'
        email.send(fail_silently=True)
        return True
    except Exception as exc:
        logger.error(f"_send_overdue_invoice_reminder: Failed to email {user.email}: {exc}")
        return False


@shared_task(bind=True, max_retries=5, default_retry_delay=30, queue='scheduling_queue')
def report_listing_overage_metered(self, subscription_id, vehicle_listing_id):
    """
    Charge one overage listing immediately via an explicit Stripe one-off invoice.

    Flow (synchronous — no webhook dependency):
      1. Create a Stripe InvoiceItem for the overage price.
      2. Create + finalize the invoice → Stripe charges the customer immediately.
      3. On successful payment, create the local Invoice record and mark
         VehicleListing.stripe_overage_reported = True.
      4. If the Stripe charge fails (payment_failed status), create a local Invoice
         with status='unpaid' and email the user.

    Idempotent: guarded by stripe_overage_reported on the listing and Stripe
    idempotency keys (idem_item / idem_inv).
    """
    from payments.models import Subscription
    from VehicleListing.models import Invoice, VehicleListing

    sub = Subscription.objects.select_related('plan', 'user').filter(pk=subscription_id).first()
    if not sub or not sub.plan or not sub.stripe_subscription_id:
        logger.warning(f"report_listing_overage_metered: subscription {subscription_id} missing or incomplete.")
        return

    listing = VehicleListing.objects.filter(pk=vehicle_listing_id, user_id=sub.user_id).first()
    if not listing:
        logger.warning(f"report_listing_overage_metered: listing {vehicle_listing_id} not found.")
        return
    if listing.stripe_overage_reported:
        logger.info(f"report_listing_overage_metered: listing {vehicle_listing_id} already reported — skip.")
        return

    if sub.status not in ('active', 'past_due', 'trialing'):
        logger.warning(
            f"report_listing_overage_metered: subscription {subscription_id} status={sub.status} — skip billing."
        )
        return

    plan = sub.plan
    if not plan.stripe_overage_price_id:
        logger.error(f"report_listing_overage_metered: plan {plan.id} has no stripe_overage_price_id.")
        return

    overage_rate = (plan.overage_rate_aud or Decimal('0.00')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    # Convert AUD dollars to cents for Stripe
    overage_cents = int(overage_rate * 100)

    stripe.api_key = settings.STRIPE_SECRET_KEY

    idem_item = f"overage-item-vl{vehicle_listing_id}"
    idem_inv = f"overage-inv-vl{vehicle_listing_id}-r{self.request.retries}"

    # Step 1: Add an explicit invoice item (one-off, not metered) so Stripe bills it immediately.
    try:
        stripe.InvoiceItem.create(
            customer=sub.stripe_customer_id,
            amount=overage_cents,
            currency='aud',
            description=f'Listing overage — vehicle listing #{vehicle_listing_id}',
            metadata={
                'source': 'listing_overage',
                'vehicle_listing_id': str(vehicle_listing_id),
                'django_subscription_id': str(subscription_id),
            },
            idempotency_key=idem_item,
        )
    except stripe.error.StripeError as exc:
        logger.exception(f"report_listing_overage_metered: InvoiceItem.create failed: {exc}")
        raise self.retry(exc=exc)

    # Step 2: Create the invoice and finalize it so Stripe charges the customer immediately.
    try:
        stripe_invoice = stripe.Invoice.create(
            customer=sub.stripe_customer_id,
            auto_advance=False,          # we finalize manually below
            collection_method='charge_automatically',
            metadata={
                'source': 'listing_overage',
                'vehicle_listing_id': str(vehicle_listing_id),
                'django_subscription_id': str(subscription_id),
            },
            description=f'Listing overage — vehicle listing #{vehicle_listing_id}',
            idempotency_key=idem_inv,
        )
    except stripe.error.StripeError as exc:
        logger.exception(f"report_listing_overage_metered: Invoice.create failed: {exc}")
        raise self.retry(exc=exc)

    stripe_invoice_id = stripe_invoice['id']

    # Step 3: Check current invoice status — on retry the idempotency key returns the same
    # invoice which may already be finalized or paid from a previous attempt.
    current_status = getattr(stripe_invoice, 'status', None)

    if current_status not in ('draft',):
        # Already finalized/paid/open from a prior attempt — retrieve fresh status
        try:
            stripe_invoice = stripe.Invoice.retrieve(stripe_invoice_id)
            current_status = getattr(stripe_invoice, 'status', None)
        except stripe.error.StripeError as exc:
            logger.exception(f"report_listing_overage_metered: retrieve invoice failed: {exc}")
            raise self.retry(exc=exc)

    if current_status == 'paid':
        payment_succeeded = True
    else:
        # Finalize only if still in draft status
        if current_status == 'draft':
            try:
                finalized = stripe.Invoice.finalize_invoice(stripe_invoice_id)
                current_status = getattr(finalized, 'status', None)
            except stripe.error.InvalidRequestError as exc:
                if 'already finalized' in str(exc).lower():
                    # Another retry beat us here — retrieve the real status
                    stripe_invoice = stripe.Invoice.retrieve(stripe_invoice_id)
                    current_status = getattr(stripe_invoice, 'status', None)
                else:
                    logger.exception(
                        f"report_listing_overage_metered: finalize failed for invoice {stripe_invoice_id}: {exc}"
                    )
                    _create_overage_invoice_record(
                        sub=sub,
                        plan=plan,
                        stripe_invoice_id=stripe_invoice_id,
                        overage_rate=overage_rate,
                        vehicle_listing_id=vehicle_listing_id,
                        paid=False,
                    )
                    raise self.retry(exc=exc)

        if current_status == 'paid':
            payment_succeeded = True
        else:
            try:
                paid_invoice = stripe.Invoice.pay(stripe_invoice_id)
                payment_succeeded = getattr(paid_invoice, 'status', None) == 'paid'
            except stripe.error.InvalidRequestError as exc:
                if 'already paid' in str(exc).lower():
                    payment_succeeded = True
                else:
                    logger.exception(
                        f"report_listing_overage_metered: pay failed for invoice {stripe_invoice_id}: {exc}"
                    )
                    _create_overage_invoice_record(
                        sub=sub,
                        plan=plan,
                        stripe_invoice_id=stripe_invoice_id,
                        overage_rate=overage_rate,
                        vehicle_listing_id=vehicle_listing_id,
                        paid=False,
                    )
                    raise self.retry(exc=exc)
            except stripe.error.StripeError as exc:
                logger.exception(
                    f"report_listing_overage_metered: pay failed for invoice {stripe_invoice_id}: {exc}"
                )
                _create_overage_invoice_record(
                    sub=sub,
                    plan=plan,
                    stripe_invoice_id=stripe_invoice_id,
                    overage_rate=overage_rate,
                    vehicle_listing_id=vehicle_listing_id,
                    paid=False,
                )
                raise self.retry(exc=exc)

    # Step 4: Create local Invoice record and mark the listing.
    _create_overage_invoice_record(
        sub=sub,
        plan=plan,
        stripe_invoice_id=stripe_invoice_id,
        overage_rate=overage_rate,
        vehicle_listing_id=vehicle_listing_id,
        paid=payment_succeeded,
    )

    logger.info(
        f"report_listing_overage_metered: charged overage for listing {vehicle_listing_id} "
        f"subscription {subscription_id} — stripe_invoice={stripe_invoice_id} paid={payment_succeeded}."
    )


def _create_overage_invoice_record(*, sub, plan, stripe_invoice_id, overage_rate, vehicle_listing_id, paid):
    """
    Create a local Invoice row for a listing overage charge and email the user.
    Marks VehicleListing.stripe_overage_reported when paid=True.
    Idempotent: skips if a record for stripe_invoice_id already exists.
    """
    from VehicleListing.models import Invoice, VehicleListing

    if Invoice.objects.filter(stripe_invoice_id=stripe_invoice_id).exists():
        logger.info(f"_create_overage_invoice_record: invoice {stripe_invoice_id} already recorded — skip.")
        return

    user = sub.user
    overage_charge = overage_rate.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    subtotal = overage_charge
    gst_amount = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total_amount = subtotal + gst_amount
    invoice_status = 'paid' if paid else 'unpaid'

    invoice = Invoice.objects.create(
        invoice_number=_next_invoice_number(),
        user=user,
        subscription=sub,
        billing_period_start=sub.current_period_start or timezone.now(),
        billing_period_end=sub.current_period_end or timezone.now(),
        plan_name=plan.name,
        base_plan_charge=Decimal('0.00'),
        included_listings=plan.listing_quota or 0,
        relist_cycles=0,
        overage_listings=1,
        overage_rate=overage_rate,
        overage_charge=overage_charge,
        discount_code=None,
        discount_code_str='',
        discount_amount=Decimal('0.00'),
        subtotal=subtotal,
        gst_amount=gst_amount,
        total_amount=total_amount,
        status=invoice_status,
        stripe_invoice_id=stripe_invoice_id,
    )

    if paid:
        _send_invoice_email(user, invoice)
        try:
            vid = int(vehicle_listing_id)
            VehicleListing.objects.filter(pk=vid, user_id=user.id).update(stripe_overage_reported=True)
        except (TypeError, ValueError):
            pass
        logger.info(
            f"_create_overage_invoice_record: created {invoice.invoice_number} for listing {vehicle_listing_id} "
            f"— total={total_amount} status=paid."
        )
    else:
        _send_payment_failed_email(user, invoice)
        # Payment failed — roll back overage_count so the dashboard stays accurate
        from django.db.models import F as _F
        from django.contrib.auth import get_user_model as _get_user_model
        _User = _get_user_model()
        _User.objects.filter(pk=user.pk, overage_count__gt=0).update(
            overage_count=_F('overage_count') - 1
        )
        logger.warning(
            f"_create_overage_invoice_record: created {invoice.invoice_number} for listing {vehicle_listing_id} "
            f"— status=unpaid (payment failed). overage_count decremented."
        )


@shared_task(queue='scheduling_queue')
def generate_listing_overage_invoice_from_webhook(subscription_id, stripe_invoice_id, vehicle_listing_id, paid=True):
    """
    Fallback: create a local Invoice for a listing-overage Stripe invoice received via webhook.
    The primary path now creates the local record synchronously in report_listing_overage_metered,
    so this task is idempotent and will be a no-op if the record already exists.
    """
    from payments.models import Subscription

    try:
        subscription_id = int(subscription_id)
    except (TypeError, ValueError):
        logger.error(f"generate_listing_overage_invoice_from_webhook: bad subscription_id={subscription_id}")
        return

    sub = Subscription.objects.select_related('plan', 'user').filter(pk=subscription_id).first()
    if not sub or not sub.plan:
        logger.error(f"generate_listing_overage_invoice_from_webhook: subscription {subscription_id} not found.")
        return

    plan = sub.plan
    overage_rate = (plan.overage_rate_aud or Decimal('0.00')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    _create_overage_invoice_record(
        sub=sub,
        plan=plan,
        stripe_invoice_id=stripe_invoice_id,
        overage_rate=overage_rate,
        vehicle_listing_id=vehicle_listing_id,
        paid=paid,
    )
