from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Create dummy invoices (paid, unpaid, overdue) for a user. Useful for dev/testing."

    def add_arguments(self, parser):
        parser.add_argument('--email', type=str, required=True, help='Email of the target user.')
        parser.add_argument('--count', type=int, default=3, help='Number of invoices per status (default: 3).')

    def handle(self, *args, **options):
        from accounts.models import User
        from payments.models import Subscription
        from payments.tasks import _next_invoice_number
        from VehicleListing.models import Invoice

        email = options['email']
        count = options['count']

        # --- Validate user ---
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User '{email}' not found."))
            return

        # --- Validate subscription & plan ---
        try:
            subscription = Subscription.objects.select_related('plan').get(user=user)
        except Subscription.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User '{email}' has no subscription."))
            return

        plan = subscription.plan
        if not plan:
            self.stdout.write(self.style.ERROR(f"User '{email}' subscription has no plan assigned."))
            return

        base_charge = plan.price_aud or Decimal('0.00')
        overage_rate = plan.overage_rate_aud or Decimal('0.00')
        listing_quota = plan.listing_quota or 0

        now = timezone.now()
        paid_count = unpaid_count = overdue_count = 0

        # ------------------------------------------------------------------ #
        # PAID invoices — billing periods in the past, fully settled          #
        # ------------------------------------------------------------------ #
        for i in range(1, count + 1):
            period_start = now - timezone.timedelta(days=30 * (count - i + 2))
            period_end = period_start + timezone.timedelta(days=30)

            # Every 3rd invoice gets a small overage for variety
            overage_listings = 5 if i % 3 == 0 else 0
            overage_charge = (Decimal(str(overage_listings)) * overage_rate).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            subtotal = (base_charge + overage_charge).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            gst_amount = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total_amount = subtotal + gst_amount

            Invoice.objects.create(
                invoice_number=_next_invoice_number(),
                user=user,
                subscription=subscription,
                billing_period_start=period_start,
                billing_period_end=period_end,
                plan_name=plan.name,
                base_plan_charge=base_charge,
                included_listings=listing_quota,
                relist_cycles=4,
                overage_listings=overage_listings,
                overage_rate=overage_rate,
                overage_charge=overage_charge,
                discount_amount=Decimal('0.00'),
                subtotal=subtotal,
                gst_amount=gst_amount,
                total_amount=total_amount,
                status='paid',
                stripe_invoice_id=f'in_seed_paid_{user.id}_{i}',
            )
            paid_count += 1
            self.stdout.write(self.style.SUCCESS(
                f"  [paid]    INV created — period {period_start.date()} → {period_end.date()}, total={total_amount}"
            ))

        # ------------------------------------------------------------------ #
        # UNPAID invoices — current period, payment not yet received          #
        # ------------------------------------------------------------------ #
        for i in range(1, count + 1):
            period_start = now - timezone.timedelta(days=i)
            period_end = now + timezone.timedelta(days=30 - i)

            subtotal = base_charge.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            gst_amount = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total_amount = subtotal + gst_amount

            Invoice.objects.create(
                invoice_number=_next_invoice_number(),
                user=user,
                subscription=subscription,
                billing_period_start=period_start,
                billing_period_end=period_end,
                plan_name=plan.name,
                base_plan_charge=base_charge,
                included_listings=listing_quota,
                relist_cycles=4,
                overage_listings=0,
                overage_rate=overage_rate,
                overage_charge=Decimal('0.00'),
                discount_amount=Decimal('0.00'),
                subtotal=subtotal,
                gst_amount=gst_amount,
                total_amount=total_amount,
                status='unpaid',
                stripe_invoice_id=f'in_seed_unpaid_{user.id}_{i}',
            )
            unpaid_count += 1
            self.stdout.write(self.style.SUCCESS(
                f"  [unpaid]  INV created — period {period_start.date()} → {period_end.date()}, total={total_amount}"
            ))

        # ------------------------------------------------------------------ #
        # OVERDUE invoices — past billing_period_end by >7 days, never paid  #
        # ------------------------------------------------------------------ #
        for i in range(1, count + 1):
            period_start = now - timezone.timedelta(days=30 * (i + 1) + 8)
            period_end = period_start + timezone.timedelta(days=30)  # >7 days ago

            overage_listings = 10 if i % 2 == 0 else 0
            overage_charge = (Decimal(str(overage_listings)) * overage_rate).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            subtotal = (base_charge + overage_charge).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            gst_amount = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total_amount = subtotal + gst_amount

            Invoice.objects.create(
                invoice_number=_next_invoice_number(),
                user=user,
                subscription=subscription,
                billing_period_start=period_start,
                billing_period_end=period_end,
                plan_name=plan.name,
                base_plan_charge=base_charge,
                included_listings=listing_quota,
                relist_cycles=4,
                overage_listings=overage_listings,
                overage_rate=overage_rate,
                overage_charge=overage_charge,
                discount_amount=Decimal('0.00'),
                subtotal=subtotal,
                gst_amount=gst_amount,
                total_amount=total_amount,
                status='overdue',
                stripe_invoice_id=None,  # simulates missed payment — no Stripe record
            )
            overdue_count += 1
            self.stdout.write(self.style.SUCCESS(
                f"  [overdue] INV created — period {period_start.date()} → {period_end.date()}, total={total_amount}"
            ))

        total = paid_count + unpaid_count + overdue_count
        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Created {total} invoices for {email} "
            f"(paid: {paid_count}, unpaid: {unpaid_count}, overdue: {overdue_count})."
        ))
