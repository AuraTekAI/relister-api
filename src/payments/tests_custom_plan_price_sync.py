"""Tests: changing a custom plan's price via PATCH pushes it to Stripe automatically.

Stripe is mocked. Covers: price change archives the old product/prices and creates
new ones; a non-price change makes no Stripe call; a Stripe failure returns 502
with the plan body (the webapp then calls retry-stripe-sync); a plan with no
Stripe IDs yet is created on price change.

Run: python src/manage.py test payments.tests_custom_plan_price_sync
"""
import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from payments.models import Plan


def _obj(**kw):
    return type('StripeObj', (), kw)()


class CustomPlanPriceSyncTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='admin@example.com', password='x')
        self.plan = Plan.objects.create(
            name='Custom A', price_aud=Decimal('99.00'), listing_quota=50,
            overage_rate_aud=Decimal('2.00'), is_custom=True,
            stripe_price_id='price_old', stripe_overage_price_id='price_old_over',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.url = reverse('admin-custom-plan-detail', args=[self.plan.id])

    def _patch(self, body):
        return self.client.patch(self.url, data=json.dumps(body), content_type='application/json')

    @mock.patch('payments.views.stripe')
    def test_price_change_archives_old_and_creates_new(self, stripe_mod):
        stripe_mod.error = mock.MagicMock()
        stripe_mod.error.StripeError = Exception
        stripe_mod.error.InvalidRequestError = type('IRE', (Exception,), {})
        stripe_mod.Price.retrieve.return_value = _obj(product='prod_old', active=True, unit_amount=9900)
        stripe_mod.Product.create.side_effect = [_obj(id='prod_new'), _obj(id='prod_new_over')]
        stripe_mod.Price.create.side_effect = [_obj(id='price_new'), _obj(id='price_new_over')]
        with mock.patch('payments.views._get_or_create_stripe_meter', return_value=_obj(id='mtr_1')):
            r = self._patch({'price_aud': '120.00'})

        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['stripe_price_id'], 'price_new')
        self.assertEqual(body['stripe_overage_price_id'], 'price_new_over')
        self.assertIn('stripe_synced', body)
        self.assertNotIn('stripe_action_required', body)
        stripe_mod.Price.modify.assert_any_call('price_old', active=False)
        stripe_mod.Price.modify.assert_any_call('price_old_over', active=False)
        stripe_mod.Product.modify.assert_called_once_with('prod_old', active=False)
        self.assertEqual(stripe_mod.Price.create.call_args_list[0].kwargs['unit_amount'], 12000)

    @mock.patch('payments.views.stripe')
    def test_non_price_change_makes_no_stripe_call(self, stripe_mod):
        r = self._patch({'name': 'Custom A renamed'})
        self.assertEqual(r.status_code, 200)
        stripe_mod.Price.create.assert_not_called()
        stripe_mod.Price.modify.assert_not_called()
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.stripe_price_id, 'price_old')

    @mock.patch('payments.views.stripe')
    def test_stripe_failure_returns_502_with_plan(self, stripe_mod):
        stripe_mod.error = mock.MagicMock()
        stripe_mod.error.StripeError = Exception
        stripe_mod.error.InvalidRequestError = type('IRE', (Exception,), {})
        stripe_mod.Price.retrieve.return_value = _obj(product='prod_old', active=True, unit_amount=9900)
        stripe_mod.Product.create.side_effect = Exception('stripe down')
        r = self._patch({'price_aud': '120.00'})
        self.assertEqual(r.status_code, 502)
        body = r.json()
        self.assertEqual(body['plan']['id'], self.plan.id)
        self.assertEqual(body['plan']['price_aud'], '120.00')  # DB kept the new price
        self.assertIsNone(body['plan']['stripe_price_id'])      # old IDs archived/cleared
        self.assertIn('warning', body)

    @mock.patch('payments.views.stripe')
    def test_price_change_moves_existing_subscribers_without_proration(self, stripe_mod):
        from payments.models import Subscription
        dealer = User.objects.create_user(email='dealer@example.com', password='x')
        sub = Subscription.objects.create(
            user=dealer, plan=self.plan, status='active',
            stripe_customer_id='cus_1', stripe_subscription_id='sub_1',
            stripe_overage_subscription_item_id='si_2',
        )
        stripe_mod.error = mock.MagicMock()
        stripe_mod.error.StripeError = Exception
        stripe_mod.error.InvalidRequestError = type('IRE', (Exception,), {})
        stripe_mod.Price.retrieve.return_value = _obj(product='prod_old', active=True, unit_amount=9900)
        stripe_mod.Product.create.side_effect = [_obj(id='prod_new'), _obj(id='prod_new_over')]
        stripe_mod.Price.create.side_effect = [_obj(id='price_new'), _obj(id='price_new_over')]
        # Subscriber is on the OLD base + overage prices.
        stripe_mod.Subscription.retrieve.return_value = _obj(id='sub_1', items=_obj(data=[
            _obj(id='si_1', price=_obj(id='price_old')),
            _obj(id='si_2', price=_obj(id='price_old_over')),
        ]))
        stripe_mod.Subscription.modify.return_value = {
            'id': 'sub_1',
            'items': {'data': [
                {'id': 'si_1', 'price': {'id': 'price_new'}},
                {'id': 'si_2', 'price': {'id': 'price_new_over'}},
            ]},
        }
        with mock.patch('payments.views._get_or_create_stripe_meter', return_value=_obj(id='mtr_1')):
            r = self._patch({'price_aud': '120.00', 'overage_rate_aud': '3.00'})

        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['subscribers_migrated'], ['dealer@example.com'])
        self.assertEqual(body['subscribers_migration_failed'], [])
        stripe_mod.Subscription.modify.assert_called_once()
        args, kwargs = stripe_mod.Subscription.modify.call_args
        self.assertEqual(args[0], 'sub_1')
        self.assertEqual(kwargs['proration_behavior'], 'none')
        self.assertEqual(kwargs['items'], [
            {'id': 'si_1', 'price': 'price_new'},
            {'id': 'si_2', 'price': 'price_new_over'},
        ])
        sub.refresh_from_db()
        self.assertEqual(sub.stripe_overage_subscription_item_id, 'si_2')

    @mock.patch('payments.views.stripe')
    def test_subscriber_move_failure_is_reported_not_raised(self, stripe_mod):
        from payments.models import Subscription
        dealer = User.objects.create_user(email='dealer2@example.com', password='x')
        Subscription.objects.create(
            user=dealer, plan=self.plan, status='active',
            stripe_customer_id='cus_2', stripe_subscription_id='sub_2',
        )
        stripe_mod.error = mock.MagicMock()
        stripe_mod.error.StripeError = Exception
        stripe_mod.error.InvalidRequestError = type('IRE', (Exception,), {})
        stripe_mod.Price.retrieve.return_value = _obj(product='prod_old', active=True, unit_amount=9900)
        stripe_mod.Product.create.side_effect = [_obj(id='prod_new'), _obj(id='prod_new_over')]
        stripe_mod.Price.create.side_effect = [_obj(id='price_new'), _obj(id='price_new_over')]
        stripe_mod.Subscription.retrieve.side_effect = Exception('stripe down')
        with mock.patch('payments.views._get_or_create_stripe_meter', return_value=_obj(id='mtr_1')):
            r = self._patch({'price_aud': '120.00'})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['stripe_price_id'], 'price_new')  # plan sync still succeeded
        self.assertEqual(body['subscribers_migrated'], [])
        self.assertEqual(body['subscribers_migration_failed'][0]['email'], 'dealer2@example.com')

    @mock.patch('payments.views.stripe')
    def test_retry_after_failed_sync_moves_subscriber_and_adds_no_second_overage(self, stripe_mod):
        """First sync fails AFTER the archive cleared the plan's Stripe IDs; the
        retry then has no 'old' IDs to match. The subscriber must still be moved
        off the archived prices, and must end up with exactly ONE overage item."""
        from payments.models import Subscription
        dealer = User.objects.create_user(email='dealer3@example.com', password='x')
        sub = Subscription.objects.create(
            user=dealer, plan=self.plan, status='active',
            stripe_customer_id='cus_3', stripe_subscription_id='sub_3',
            stripe_overage_subscription_item_id='si_2',
        )
        stripe_mod.error = mock.MagicMock()
        stripe_mod.error.StripeError = Exception
        stripe_mod.error.InvalidRequestError = type('IRE', (Exception,), {})
        stripe_mod.Price.retrieve.return_value = _obj(product='prod_old', active=True, unit_amount=9900)
        # 1) PATCH: archive succeeds, product creation fails → 502, IDs cleared.
        stripe_mod.Product.create.side_effect = Exception('stripe down')
        r = self._patch({'price_aud': '120.00'})
        self.assertEqual(r.status_code, 502)
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.stripe_price_id)
        self.assertIsNone(self.plan.stripe_overage_price_id)

        # 2) Retry: Stripe is back. Subscriber is still on the ARCHIVED prices.
        stripe_mod.Product.create.side_effect = [_obj(id='prod_new'), _obj(id='prod_new_over')]
        stripe_mod.Price.create.side_effect = [_obj(id='price_new'), _obj(id='price_new_over')]
        stripe_mod.Subscription.retrieve.return_value = _obj(id='sub_3', items=_obj(data=[
            _obj(id='si_1', price=_obj(id='price_old', recurring=_obj(usage_type='licensed'))),
            _obj(id='si_2', price=_obj(id='price_old_over', recurring=_obj(usage_type='metered'))),
        ]))
        stripe_mod.Subscription.modify.return_value = {
            'id': 'sub_3',
            'items': {'data': [
                {'id': 'si_1', 'price': {'id': 'price_new'}},
                {'id': 'si_2', 'price': {'id': 'price_new_over'}},
            ]},
        }
        retry_url = reverse('admin-custom-plan-retry-stripe-sync', args=[self.plan.id])
        with mock.patch('payments.views._get_or_create_stripe_meter', return_value=_obj(id='mtr_1')):
            r = self.client.post(retry_url)

        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['stripe_price_id'], 'price_new')
        self.assertEqual(body['subscribers_migrated'], ['dealer3@example.com'])
        stripe_mod.Subscription.modify.assert_called_once()
        _, kwargs = stripe_mod.Subscription.modify.call_args
        self.assertEqual(kwargs['proration_behavior'], 'none')
        # Old base → new base, old overage → new overage. No bare {'price': ...}
        # entry, i.e. no SECOND overage item that would bill extra listings twice.
        self.assertEqual(kwargs['items'], [
            {'id': 'si_1', 'price': 'price_new'},
            {'id': 'si_2', 'price': 'price_new_over'},
        ])
        self.assertFalse(any('id' not in it for it in kwargs['items']))
        sub.refresh_from_db()
        self.assertEqual(sub.stripe_overage_subscription_item_id, 'si_2')

    @mock.patch('payments.views.stripe')
    def test_duplicate_stale_overage_item_is_removed(self, stripe_mod):
        """A subscriber left with two overage items by an earlier bad retry ends
        up with exactly one (the current price) — the stale one is deleted."""
        from payments.models import Subscription
        dealer = User.objects.create_user(email='dealer4@example.com', password='x')
        Subscription.objects.create(
            user=dealer, plan=self.plan, status='active',
            stripe_customer_id='cus_4', stripe_subscription_id='sub_4',
        )
        stripe_mod.error = mock.MagicMock()
        stripe_mod.error.StripeError = Exception
        stripe_mod.error.InvalidRequestError = type('IRE', (Exception,), {})
        stripe_mod.Price.retrieve.return_value = _obj(product='prod_old', active=True, unit_amount=9900)
        stripe_mod.Product.create.side_effect = [_obj(id='prod_new'), _obj(id='prod_new_over')]
        stripe_mod.Price.create.side_effect = [_obj(id='price_new'), _obj(id='price_new_over')]
        stripe_mod.Subscription.retrieve.return_value = _obj(id='sub_4', items=_obj(data=[
            _obj(id='si_1', price=_obj(id='price_old', recurring=_obj(usage_type='licensed'))),
            _obj(id='si_2', price=_obj(id='price_old_over', recurring=_obj(usage_type='metered'))),
            _obj(id='si_3', price=_obj(id='price_older_over', recurring=_obj(usage_type='metered'))),
        ]))
        stripe_mod.Subscription.modify.return_value = {
            'id': 'sub_4',
            'items': {'data': [
                {'id': 'si_1', 'price': {'id': 'price_new'}},
                {'id': 'si_2', 'price': {'id': 'price_new_over'}},
            ]},
        }
        with mock.patch('payments.views._get_or_create_stripe_meter', return_value=_obj(id='mtr_1')):
            r = self._patch({'price_aud': '120.00'})
        self.assertEqual(r.status_code, 200, r.content)
        _, kwargs = stripe_mod.Subscription.modify.call_args
        self.assertEqual(kwargs['items'], [
            {'id': 'si_1', 'price': 'price_new'},
            {'id': 'si_2', 'price': 'price_new_over'},
            {'id': 'si_3', 'deleted': True},
        ])

    @mock.patch('payments.views.stripe')
    def test_plan_without_stripe_ids_is_created_on_price_change(self, stripe_mod):
        self.plan.stripe_price_id = None
        self.plan.stripe_overage_price_id = None
        self.plan.overage_rate_aud = None
        self.plan.save()
        stripe_mod.error = mock.MagicMock()
        stripe_mod.error.StripeError = Exception
        stripe_mod.Product.create.return_value = _obj(id='prod_new')
        stripe_mod.Price.create.return_value = _obj(id='price_new')
        r = self._patch({'price_aud': '150.00'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['stripe_price_id'], 'price_new')
        stripe_mod.Price.retrieve.assert_not_called()
        stripe_mod.Price.modify.assert_not_called()


class InvoiceAmountsFromStripeTests(TestCase):
    """Invoice records mirror what Stripe actually charged, not the plan's DB values."""

    def setUp(self):
        self.plan = Plan.objects.create(
            name='Custom B', price_aud=Decimal('120.00'), listing_quota=50,
            overage_rate_aud=Decimal('3.00'), is_custom=True,
            stripe_price_id='price_new', stripe_overage_price_id='price_new_over',
        )

    def _invoice(self, base_price_id, base_cents, over_price_id, over_unit_cents, over_amount_cents, qty):
        return {'lines': {'data': [
            {'price': {'id': base_price_id, 'unit_amount': base_cents, 'recurring': {'usage_type': 'licensed'}},
             'amount': base_cents, 'quantity': 1},
            {'price': {'id': over_price_id, 'unit_amount': over_unit_cents, 'recurring': {'usage_type': 'metered'}},
             'amount': over_amount_cents, 'quantity': qty},
            {'price': {'id': 'price_once', 'unit_amount': 500, 'recurring': None}, 'amount': 500, 'quantity': 1},
        ]}}

    def _basil_invoice(self, base_price_id, base_cents, over_price_id, over_unit_cents, over_amount_cents, qty,
                       over_item='si_over'):
        """Invoice as Stripe API 2025-03-31.basil+ returns it: lines carry NO
        `price` — only `pricing.price_details.price` (an id) and `parent`."""
        return {'lines': {'data': [
            {'pricing': {'price_details': {'price': base_price_id, 'product': 'prod_b'},
                         'type': 'price_details', 'unit_amount_decimal': str(base_cents)},
             'parent': {'type': 'subscription_item_details',
                        'subscription_item_details': {'subscription_item': 'si_base'}},
             'amount': base_cents, 'quantity': 1},
            {'pricing': {'price_details': {'price': over_price_id, 'product': 'prod_o'},
                         'type': 'price_details', 'unit_amount_decimal': str(over_unit_cents)},
             'parent': {'type': 'subscription_item_details',
                        'subscription_item_details': {'subscription_item': over_item}},
             'amount': over_amount_cents, 'quantity': qty},
        ]}}

    @staticmethod
    def _price_lookup(prices):
        def retrieve(pid, **_):
            if pid not in prices:
                raise Exception(f'No such price: {pid}')
            return prices[pid]
        return retrieve

    @mock.patch('payments.stripe_utils.stripe')
    def test_basil_invoice_lines_without_price_field_are_read(self, stripe_mod):
        """Motoroo case: new Stripe API shape. Base AND extra listings must both be read."""
        from payments.stripe_utils import (
            extract_base_charge_from_stripe_invoice,
            extract_overage_rate_from_stripe_invoice,
            extract_metered_overage_from_stripe_invoice,
        )
        stripe_mod.Price.retrieve.side_effect = self._price_lookup({
            'price_old': {'id': 'price_old', 'unit_amount': 9900, 'recurring': {'usage_type': 'licensed'}},
            'price_old_over': {'id': 'price_old_over', 'unit_amount': 350, 'recurring': {'usage_type': 'metered'}},
        })
        inv = self._basil_invoice('price_old', 9900, 'price_old_over', 350, 6650, 19)
        self.assertEqual(extract_base_charge_from_stripe_invoice(inv, self.plan), Decimal('99.00'))
        rate = extract_overage_rate_from_stripe_invoice(inv, self.plan)
        self.assertEqual(rate, Decimal('3.50'))
        count, charge = extract_metered_overage_from_stripe_invoice(inv, self.plan, rate=rate)
        self.assertEqual((count, charge), (19, Decimal('66.50')))

    @mock.patch('payments.stripe_utils.stripe')
    def test_overage_read_via_subscription_item_when_plan_overage_id_missing(self, stripe_mod):
        """Plan lost its overage price id (failed sync) and the Price fetch fails:
        the line is still recognised through the subscription's overage item and
        the rate comes from the line's own unit_amount_decimal."""
        from payments.stripe_utils import (
            extract_overage_rate_from_stripe_invoice,
            extract_metered_overage_from_stripe_invoice,
        )
        self.plan.stripe_overage_price_id = None
        self.plan.save()
        sub = _obj(stripe_overage_subscription_item_id='si_over')
        stripe_mod.Price.retrieve.side_effect = self._price_lookup({
            'price_old': {'id': 'price_old', 'unit_amount': 9900, 'recurring': {'usage_type': 'licensed'}},
        })
        inv = self._basil_invoice('price_old', 9900, 'price_gone', 350, 6650, 19, over_item='si_over')
        rate = extract_overage_rate_from_stripe_invoice(inv, self.plan, subscription=sub)
        self.assertEqual(rate, Decimal('3.50'))
        count, charge = extract_metered_overage_from_stripe_invoice(inv, self.plan, rate=rate, subscription=sub)
        self.assertEqual((count, charge), (19, Decimal('66.50')))

    @mock.patch('payments.stripe_utils.stripe')
    @mock.patch('payments.tasks.stripe')
    def test_generate_invoice_records_extra_listings_on_basil_api(self, tasks_stripe, utils_stripe):
        """End to end: the recorded invoice carries base + extra listings + GST,
        and the retrieve is made WITHOUT the removed `lines.data.price` expand."""
        from payments.models import Subscription
        from payments.tasks import generate_invoice
        from VehicleListing.models import Invoice
        dealer = User.objects.create_user(email='motoroo@example.com', password='x')
        sub = Subscription.objects.create(
            user=dealer, plan=self.plan, status='active',
            stripe_customer_id='cus_m', stripe_subscription_id='sub_m',
            stripe_overage_subscription_item_id='si_over',
        )
        tasks_stripe.error = mock.MagicMock()
        tasks_stripe.error.StripeError = Exception
        tasks_stripe.Invoice.retrieve.return_value = self._basil_invoice('price_old', 9900, 'price_old_over', 350, 6650, 19)
        utils_stripe.Price.retrieve.side_effect = self._price_lookup({
            'price_old': {'id': 'price_old', 'unit_amount': 9900, 'recurring': {'usage_type': 'licensed'}},
            'price_old_over': {'id': 'price_old_over', 'unit_amount': 350, 'recurring': {'usage_type': 'metered'}},
        })
        with mock.patch('payments.tasks._send_invoice_email'):
            generate_invoice(sub.id, stripe_invoice_id='in_m', paid=True)
        _, kwargs = tasks_stripe.Invoice.retrieve.call_args
        self.assertNotIn('expand', kwargs)
        inv = Invoice.objects.get(stripe_invoice_id='in_m')
        self.assertEqual(inv.base_plan_charge, Decimal('99.00'))
        self.assertEqual(inv.overage_listings, 19)
        self.assertEqual(inv.overage_rate, Decimal('3.50'))
        self.assertEqual(inv.overage_charge, Decimal('66.50'))
        self.assertEqual(inv.subtotal, Decimal('165.50'))
        self.assertEqual(inv.gst_amount, Decimal('16.55'))
        self.assertEqual(inv.total_amount, Decimal('182.05'))

    def test_webhook_subscription_id_read_from_basil_parent(self):
        from payments.views import _invoice_subscription_id
        self.assertEqual(_invoice_subscription_id({'subscription': 'sub_1'}), 'sub_1')
        self.assertEqual(_invoice_subscription_id(
            {'subscription': None, 'parent': {'subscription_details': {'subscription': 'sub_2'}}}), 'sub_2')
        self.assertIsNone(_invoice_subscription_id({'subscription': None, 'parent': None}))

    def test_reads_old_prices_when_subscriber_still_on_archived_price(self):
        from payments.stripe_utils import (
            extract_base_charge_from_stripe_invoice,
            extract_overage_rate_from_stripe_invoice,
            extract_metered_overage_from_stripe_invoice,
        )
        inv = self._invoice('price_old', 9900, 'price_old_over', 200, 800, 4)
        self.assertEqual(extract_base_charge_from_stripe_invoice(inv, self.plan), Decimal('99.00'))
        rate = extract_overage_rate_from_stripe_invoice(inv, self.plan)
        self.assertEqual(rate, Decimal('2.00'))
        count, charge = extract_metered_overage_from_stripe_invoice(inv, self.plan, rate=rate)
        self.assertEqual((count, charge), (4, Decimal('8.00')))

    def test_reads_current_prices_after_migration(self):
        from payments.stripe_utils import (
            extract_base_charge_from_stripe_invoice,
            extract_overage_rate_from_stripe_invoice,
        )
        inv = self._invoice('price_new', 12000, 'price_new_over', 300, 900, 3)
        self.assertEqual(extract_base_charge_from_stripe_invoice(inv, self.plan), Decimal('120.00'))
        self.assertEqual(extract_overage_rate_from_stripe_invoice(inv, self.plan), Decimal('3.00'))

    @mock.patch('payments.tasks.stripe')
    def test_generate_invoice_records_stripe_amounts(self, stripe_mod):
        from payments.models import Subscription
        from payments.tasks import generate_invoice
        from VehicleListing.models import Invoice
        dealer = User.objects.create_user(email='dealer3@example.com', password='x')
        sub = Subscription.objects.create(
            user=dealer, plan=self.plan, status='active',
            stripe_customer_id='cus_3', stripe_subscription_id='sub_3',
        )
        stripe_mod.error = mock.MagicMock()
        stripe_mod.error.StripeError = Exception
        stripe_mod.Invoice.retrieve.return_value = self._invoice('price_old', 9900, 'price_old_over', 200, 800, 4)
        with mock.patch('payments.tasks._send_invoice_email'):
            generate_invoice(sub.id, stripe_invoice_id='in_1', paid=True)
        inv = Invoice.objects.get(stripe_invoice_id='in_1')
        self.assertEqual(inv.base_plan_charge, Decimal('99.00'))
        self.assertEqual(inv.overage_rate, Decimal('2.00'))
        self.assertEqual(inv.overage_listings, 4)
        self.assertEqual(inv.overage_charge, Decimal('8.00'))
        self.assertEqual(inv.subtotal, Decimal('107.00'))
