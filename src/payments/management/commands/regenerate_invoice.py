"""
Re-read a Stripe invoice and rebuild our local Invoice record from what Stripe
actually charged (base plan + extra listings + GST).

Why: before the basil-API fix in generate_invoice, the extra-listings line was
silently dropped and the record only carried the monthly plan amount. This
command repairs such records in place — the invoice number, status and
created_at are preserved; only the amounts change. No email is sent unless
--email is passed.

Usage (on the server, inside the web container):
    python manage.py regenerate_invoice --stripe-invoice-id in_XXXX --dry-run   # show old vs new, write nothing
    python manage.py regenerate_invoice --stripe-invoice-id in_XXXX             # apply
    python manage.py regenerate_invoice --invoice-number INV-000123 --dry-run   # same, by our number
"""
from decimal import Decimal
from unittest import mock

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Rebuild a local invoice's amounts from the real Stripe invoice (base + extra listings + GST)."

    def add_arguments(self, parser):
        parser.add_argument('--stripe-invoice-id', type=str, help='Stripe invoice id (in_...).')
        parser.add_argument('--invoice-number', type=str, help='Our invoice number (alternative to --stripe-invoice-id).')
        parser.add_argument('--dry-run', action='store_true', help='Print old vs new amounts; change nothing.')
        parser.add_argument('--email', action='store_true', help='Also re-send the invoice email to the customer (off by default).')

    def handle(self, *args, **options):
        from VehicleListing.models import Invoice
        from payments import tasks as payment_tasks

        sid = options.get('stripe_invoice_id')
        number = options.get('invoice_number')
        if not sid and not number:
            raise CommandError('Pass --stripe-invoice-id or --invoice-number.')

        qs = Invoice.objects.select_related('subscription', 'user')
        old = qs.filter(stripe_invoice_id=sid).first() if sid else qs.filter(invoice_number=number).first()
        if not old:
            raise CommandError('No local invoice matches.')
        if not old.stripe_invoice_id:
            raise CommandError(f'{old.invoice_number} has no stripe_invoice_id — nothing to re-read.')
        if not old.subscription_id:
            raise CommandError(f'{old.invoice_number} has no subscription — cannot regenerate.')

        self.stdout.write(
            f"{old.invoice_number} ({old.user.email}) — current record: base={old.base_plan_charge} "
            f"extra={old.overage_listings} × {old.overage_rate} = {old.overage_charge} "
            f"discount={old.discount_amount} subtotal={old.subtotal} gst={old.gst_amount} total={old.total_amount}"
        )

        keep = {
            'invoice_number': old.invoice_number,
            'status': old.status,
            'created_at': old.created_at,
            'stripe_invoice_id': old.stripe_invoice_id,
            'subscription_id': old.subscription_id,
        }
        paid = old.status == 'paid'
        email_patch = (
            mock.patch.object(payment_tasks, '_send_invoice_email') if not options['email'] else _NoopCtx()
        )
        failed_patch = mock.patch.object(payment_tasks, '_send_payment_failed_email')

        with transaction.atomic():
            old.delete()
            with email_patch, failed_patch, mock.patch.object(
                payment_tasks, '_next_invoice_number', return_value=keep['invoice_number']
            ):
                # Called synchronously (not .delay) so the result is visible here.
                payment_tasks.generate_invoice(keep['subscription_id'], stripe_invoice_id=keep['stripe_invoice_id'], paid=paid)

            new = Invoice.objects.filter(stripe_invoice_id=keep['stripe_invoice_id']).first()
            if not new:
                raise CommandError('generate_invoice did not create a record — see logs; nothing changed (rolled back).')

            # Preserve identity: same number, same status, same creation time.
            Invoice.objects.filter(pk=new.pk).update(
                invoice_number=keep['invoice_number'], status=keep['status'], created_at=keep['created_at']
            )
            new.refresh_from_db()

            self.stdout.write(
                f"{new.invoice_number} — from Stripe:     base={new.base_plan_charge} "
                f"extra={new.overage_listings} × {new.overage_rate} = {new.overage_charge} "
                f"discount={new.discount_amount} subtotal={new.subtotal} gst={new.gst_amount} total={new.total_amount}"
            )
            delta = (new.total_amount or Decimal('0')) - (old.total_amount or Decimal('0'))
            self.stdout.write(f"total change: {delta:+}")

            if options['dry_run']:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('DRY RUN — rolled back, nothing changed.'))
                return

        self.stdout.write(self.style.SUCCESS(f'{new.invoice_number} updated to match Stripe.'))


class _NoopCtx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
