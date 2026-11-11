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
