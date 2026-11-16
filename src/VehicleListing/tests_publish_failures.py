"""Tests for the server-side failed-publish history (/publish-failures/).

Covers: a failure increments (never resets), the hold threshold at 2, the
sticky unknown-outcome flag, history import via at_least_fail_count, DELETE
after a confirmed publish, per-user isolation, and ownership checks.
"""
import json

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from VehicleListing.models import VehicleListing, PublishFailure


class PublishFailuresEndpointTests(TestCase):
    def setUp(self):
        self.dealer = User.objects.create_user(email='dealer@example.com', password='x')
        self.other = User.objects.create_user(email='other@example.com', password='x')
        self.listing = VehicleListing.objects.create(user=self.dealer, list_id='L1', seller_profile_id='S1')
        self.foreign = VehicleListing.objects.create(user=self.other, list_id='L2', seller_profile_id='S2')
        self.client = APIClient()
        self.client.force_authenticate(user=self.dealer)
        self.url = reverse('publish_failures')

    def _post(self, body):
        return self.client.post(self.url, data=json.dumps(body), content_type='application/json')

    def _delete(self, body):
        return self.client.delete(self.url, data=json.dumps(body), content_type='application/json')

    def test_failures_accumulate_and_hold_at_two(self):
        r1 = self._post({'id': self.listing.id, 'reason': 'model missing'})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()['data']['fail_count'], 1)
        self.assertFalse(r1.json()['data']['is_held'])

        r2 = self._post({'id': self.listing.id, 'reason': 'model missing again'})
        self.assertEqual(r2.json()['data']['fail_count'], 2)
        self.assertTrue(r2.json()['data']['is_held'])
        self.assertEqual(r2.json()['data']['reason'], 'model missing again')

        # A third failure (e.g. a failed "Retry now") keeps counting — nothing resets it.
        r3 = self._post({'id': self.listing.id})
        self.assertEqual(r3.json()['data']['fail_count'], 3)
        self.assertEqual(r3.json()['data']['reason'], 'model missing again')  # reason kept when none sent

    def test_unknown_outcome_is_sticky_and_holds_immediately(self):
        r = self._post({'id': self.listing.id, 'unknown_outcome': True, 'reason': 'fb unreadable'})
        self.assertEqual(r.json()['data']['fail_count'], 1)
        self.assertTrue(r.json()['data']['unknown_outcome'])
        self.assertTrue(r.json()['data']['is_held'])
        r = self._post({'id': self.listing.id, 'unknown_outcome': False})
        self.assertTrue(r.json()['data']['unknown_outcome'])

    def test_import_raises_but_never_lowers_or_increments(self):
        self._post({'id': self.listing.id})
        r = self._post({'id': self.listing.id, 'at_least_fail_count': 2})
        self.assertEqual(r.json()['data']['fail_count'], 2)
        r = self._post({'id': self.listing.id, 'at_least_fail_count': 1})
        self.assertEqual(r.json()['data']['fail_count'], 2)

    def test_get_lists_only_callers_rows(self):
        self._post({'id': self.listing.id})
        PublishFailure.objects.create(user=self.other, listing=self.foreign, fail_count=5,
                                      last_fail_at=PublishFailure.objects.get(listing=self.listing).last_fail_at)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['hold_threshold'], 2)
        self.assertEqual(set(r.json()['data'].keys()), {str(self.listing.id)})

    def test_delete_clears_and_is_idempotent(self):
        self._post({'id': self.listing.id})
        r = self._delete({'id': self.listing.id})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['data']['cleared'])
        self.assertFalse(PublishFailure.objects.filter(listing=self.listing).exists())
        r = self._delete({'id': self.listing.id})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['data']['cleared'])

    def test_cannot_touch_another_dealers_listing(self):
        self.assertEqual(self._post({'id': self.foreign.id}).status_code, 404)
        self.assertEqual(self._delete({'id': self.foreign.id}).status_code, 404)
        self.assertFalse(PublishFailure.objects.filter(listing=self.foreign).exists())

    def test_validation(self):
        self.assertEqual(self._post({}).status_code, 400)
        self.assertEqual(self._post({'id': 'abc'}).status_code, 400)
        self.assertEqual(self._post({'id': self.listing.id, 'at_least_fail_count': -1}).status_code, 400)
        self.assertEqual(APIClient().get(self.url).status_code, 401)
