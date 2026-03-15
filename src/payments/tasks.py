import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from celery import shared_task
from django.utils import timezone

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
    # TODO: listing_count is reset to 0 each billing cycle via the Stripe webhook.
    # No publish tracking mechanism exists yet — overage will always be 0 until implemented.
    listings_used = subscription.listing_count
    overage_count = max(0, listings_used - listing_quota)
    overage_charge = (Decimal(str(overage_count)) * overage_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # --- Discount ---
    discount_obj = subscription.active_discount_code
    discount_amount = Decimal('0.00')
    if discount_obj and discount_obj.is_valid():
        pre_discount = base_charge + overage_charge
        if discount_obj.discount_type == 'percentage':
            discount_amount = (pre_discount * discount_obj.discount_value / Decimal('100')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        else:
            discount_amount = min(discount_obj.discount_value, pre_discount)
        # Increment usage count
        discount_obj.used_count += 1
        discount_obj.save(update_fields=['used_count'])
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
        from relister.settings import EMAIL_HOST_USER

        subject = f"Invoice {invoice.invoice_number} — Payment Confirmed"
        body = render_to_string('payments/invoice_email.html', {
            'user': user,
            'invoice': invoice,
        })
        email = EmailMessage(subject, body, EMAIL_HOST_USER, [user.email])
        email.content_subtype = 'html'
        email.send(fail_silently=True)
    except Exception as exc:
        logger.error(f"_send_invoice_email: Failed to send invoice email to {user.email}: {exc}")


def _send_payment_failed_email(user, invoice):
    """Send a payment failed alert — always sent regardless of notification preferences."""
    try:
        from django.core.mail import EmailMessage
        from django.template.loader import render_to_string
        from relister.settings import EMAIL_HOST_USER

        subject = f"Action Required: Payment Failed for Invoice {invoice.invoice_number}"
        body = render_to_string('payments/payment_failed_email.html', {
            'user': user,
            'invoice': invoice,
        })
        email = EmailMessage(subject, body, EMAIL_HOST_USER, [user.email])
        email.content_subtype = 'html'
        email.send(fail_silently=True)
    except Exception as exc:
        logger.error(f"_send_payment_failed_email: Failed to send payment failed email to {user.email}: {exc}")


@shared_task(bind=True, queue='scheduling_queue', max_retries=3, default_retry_delay=15)
def generate_invoice_delayed(self, stripe_subscription_id, stripe_invoice_id=None):
    """
    Delayed retry for generate_invoice when invoice.payment_succeeded arrives before
    checkout.session.completed has written the Subscription row (race condition on first checkout).
    Retries up to 3 times with a 15-second delay between attempts.
    """
    from .models import Subscription

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=stripe_subscription_id)
    except Subscription.DoesNotExist:
        logger.warning(
            f"generate_invoice_delayed: Subscription {stripe_subscription_id} still not found "
            f"(attempt {self.request.retries + 1}/3) — retrying."
        )
        raise self.retry()

    logger.info(f"generate_invoice_delayed: Subscription {stripe_subscription_id} found — generating invoice.")
    generate_invoice.delay(subscription.id, stripe_invoice_id=stripe_invoice_id, paid=True)


@shared_task(bind=True, queue='scheduling_queue')
def mark_overdue_invoices(self):
    """
    Periodic task — runs daily.
    Marks unpaid invoices as 'overdue' if they are more than 7 days past their billing_period_end.
    Scheduled via django-celery-beat.
    """
    from VehicleListing.models import Invoice

    cutoff = timezone.now() - timedelta(days=7)
    updated = Invoice.objects.filter(
        status='unpaid',
        billing_period_end__lt=cutoff,
    ).update(status='overdue')

    logger.info(f"mark_overdue_invoices: Marked {updated} invoice(s) as overdue (cutoff={cutoff.date()}).")
