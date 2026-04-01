import stripe
from django.conf import settings
from django.core.management.base import BaseCommand
from payments.models import Plan


class Command(BaseCommand):
    help = "Seed Stripe products/prices and local Plan records. Safe to run multiple times (idempotent)."

    PLANS = [
        {
            'name': 'Starter',
            'price_aud': '599.00',
            'listing_quota': 50,
            'overage_rate_aud': '3.50',
        },
        {
            'name': 'Professional',
            'price_aud': '999.00',
            'listing_quota': 100,
            'overage_rate_aud': '3.25',
        },
    ]

    def handle(self, *args, **options):
        stripe.api_key = settings.STRIPE_SECRET_KEY

        # Clean up any partially-created Stripe products from a previous failed run
        self._cleanup_orphaned_stripe_products()

        for plan_data in self.PLANS:
            name = plan_data['name']

            # Idempotency — skip if already seeded
            if Plan.objects.filter(name=name).exists():
                self.stdout.write(f"Plan '{name}' already exists — skipping.")
                continue

            stripe_price_id = None
            stripe_overage_price_id = None

            if name != 'Enterprise':
                # Create Stripe product
                product = stripe.Product.create(
                    name=f"Relister {name}",
                    metadata={'plan_name': name},
                )
                self.stdout.write(f"Created Stripe product: {product.id}")

                # Base monthly recurring price (licensed/flat fee) in AUD cents
                price_cents = int(float(plan_data['price_aud']) * 100)
                base_price = stripe.Price.create(
                    product=product.id,
                    unit_amount=price_cents,
                    currency='aud',
                    recurring={'interval': 'month'},
                    metadata={'type': 'base_monthly'},
                )
                stripe_price_id = base_price.id
                self.stdout.write(f"Created base price: {stripe_price_id}")

                # Stripe API >= 2025-03-31.basil: metered prices must be backed by a Meter.
                # Create (or reuse) a meter for this plan's overage events.
                meter_name = f"relister_{name.lower()}_overage"
                meter = self._get_or_create_meter(meter_name)
                self.stdout.write(f"Using meter: {meter.id} ({meter_name})")

                # Overage — metered per-unit price backed by the meter above.
                overage_cents = int(float(plan_data['overage_rate_aud']) * 100)
                overage_price = stripe.Price.create(
                    product=product.id,
                    unit_amount=overage_cents,
                    currency='aud',
                    recurring={
                        'interval': 'month',
                        'meter': meter.id,
                        'usage_type': 'metered',
                    },
                    billing_scheme='per_unit',
                    metadata={
                        'type': 'overage_per_listing',
                        'meter_event_name': meter_name,
                    },
                )
                stripe_overage_price_id = overage_price.id
                self.stdout.write(f"Created overage price: {stripe_overage_price_id}")

            Plan.objects.create(
                name=name,
                stripe_price_id=stripe_price_id,
                stripe_overage_price_id=stripe_overage_price_id,
                price_aud=plan_data['price_aud'],
                listing_quota=plan_data['listing_quota'],
                overage_rate_aud=plan_data['overage_rate_aud'],
                is_active=True,
            )
            self.stdout.write(self.style.SUCCESS(f"Plan '{name}' created successfully."))

        self.stdout.write(self.style.SUCCESS("seed_plans complete."))

    def _get_or_create_meter(self, event_name):
        """
        Return an existing active Stripe Meter with the given event_name, or create one.
        Meters are reusable across runs — idempotent by event_name.
        """
        # List existing meters and find a match
        meters = stripe.billing.Meter.list(limit=100)
        for m in meters.auto_paging_iter():
            if m.event_name == event_name and m.status == 'active':
                return m

        # None found — create a new one
        return stripe.billing.Meter.create(
            display_name=f"Relister overage meter ({event_name})",
            event_name=event_name,
            default_aggregation={'formula': 'sum'},
            value_settings={'event_payload_key': 'value'},
        )

    def _cleanup_orphaned_stripe_products(self):
        """
        Archive any Stripe products named 'Relister Starter' or 'Relister Professional'
        that were created by a previously failed seed run but have no matching DB Plan record.
        This prevents duplicate products in Stripe on retry.
        """
        plan_names_in_db = set(Plan.objects.values_list('name', flat=True))
        products_to_check = ['Starter', 'Professional']

        for plan_name in products_to_check:
            if plan_name in plan_names_in_db:
                continue  # DB record exists, this was a successful seed — leave it alone

            # Search Stripe for orphaned products from a failed run
            stripe_name = f"Relister {plan_name}"
            products = stripe.Product.search(query=f'name:"{stripe_name}"')
            for product in products.data:
                meta = getattr(product, 'metadata', None)
                meta_plan = getattr(meta, 'plan_name', None) if meta is not None else None
                is_active = getattr(product, 'active', False)
                if meta_plan == plan_name and is_active:
                    stripe.Product.modify(product.id, active=False)
                    self.stdout.write(f"Archived orphaned Stripe product: {product.id} ({stripe_name})")
