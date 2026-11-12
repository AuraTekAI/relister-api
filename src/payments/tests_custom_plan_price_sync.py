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
