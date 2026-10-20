"""Tests for Gumtree sync-run tracking + the Gumtree⇄database reconciliation.

Covers: a successful sync (records the run + returned ad ids), a failed sync
(records 'failed', never read as "no ads"), an empty profile, and the
listing-count reconciliation identity (gumtree_total == matched + missing).
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.utils import timezone

from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from VehicleListing.models import (
    VehicleListing, GumtreeProfileListing, GumtreeSyncRun,
    FacebookListingSnapshot, UnpublishedListingSnapshot,
)
from VehicleListing import gumtree_scraper
from VehicleListing.gumtree_reconciliation import (
    build_gumtree_reconciliation,
    REASON_NOT_YET_SYNCED, REASON_SOLD_OR_REMOVED, REASON_SYNC_FAILURE, REASON_UNKNOWN,
)


class _FakeResp:
    def __init__(self, status_code, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json
        self.text = '' if payload is None else str(payload)

    def json(self):
        if self._bad_json:
            raise ValueError('not json')
        return self._payload


def _fake_client(count_resp, full_resp=None):
    """A fake ZenRowsClient: the count request carries size=1, the full request
    carries the real size."""
    client = MagicMock()

    def _get(url, headers=None):
        return count_resp if 'size=1' in url else full_resp
    client.get.side_effect = _get
    return client


# --- Sync-run recording -------------------------------------------------

@override_settings(ZENROWS_API_KEY='test-key')
class GumtreeSyncRunRecordingTests(TestCase):
    URL = 'https://www.gumtree.com.au/sellerprofile/555'

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@example.com', password='x')

    @patch.object(gumtree_scraper, 'gumtree_profile_listings_thread')  # don't run the detail thread
    @patch.object(gumtree_scraper, 'ZenRowsClient')
    def test_successful_sync_records_run_with_returned_ids(self, MockClient, _thread):
        MockClient.return_value = _fake_client(
            _FakeResp(200, {'totalCount': 2}),
            _FakeResp(200, {'profileListingList': [{'id': 'ad-1'}, {'id': 'ad-2'}]}),
        )
        ok, _msg = gumtree_scraper.get_gumtree_listings(self.URL, self.user, source='manual')
        self.assertTrue(ok)
        run = GumtreeSyncRun.objects.get(user=self.user)
        self.assertEqual(run.outcome, 'success')
        self.assertEqual(run.source, 'manual')
        self.assertEqual(run.fetched_count, 2)
        self.assertEqual(sorted(run.returned_list_ids), ['ad-1', 'ad-2'])

    @patch.object(gumtree_scraper, 'ZenRowsClient')
    def test_failed_fetch_records_failed_not_empty(self, MockClient):
        # ZenRows 402 (quota) on the count request.
        MockClient.return_value = _fake_client(_FakeResp(402, {}))
        ok, _msg = gumtree_scraper.get_gumtree_listings(self.URL, self.user)
        self.assertFalse(ok)
        run = GumtreeSyncRun.objects.get(user=self.user)
        self.assertEqual(run.outcome, 'failed')   # NOT 'empty'
        self.assertEqual(run.http_status, 402)
        self.assertEqual(run.returned_list_ids, [])

    @patch.object(gumtree_scraper, 'ZenRowsClient')
    def test_empty_profile_records_empty(self, MockClient):
        MockClient.return_value = _fake_client(_FakeResp(200, {'totalCount': 0}))
        ok, _msg = gumtree_scraper.get_gumtree_listings(self.URL, self.user)
        self.assertFalse(ok)
        run = GumtreeSyncRun.objects.get(user=self.user)
        self.assertEqual(run.outcome, 'empty')


# --- Reconciliation -----------------------------------------------------

class GumtreeReconciliationTests(TestCase):
    SELLER = '555'

    def setUp(self):
        self.user = User.objects.create_user(email='dealer2@example.com', password='x')
        self.profile = GumtreeProfileListing.objects.create(
            user=self.user, url='https://g/555', profile_id=self.SELLER, status='completed'
        )

    def _vl(self, list_id, status='completed', sales=False):
        return VehicleListing.objects.create(
            user=self.user, list_id=list_id, seller_profile_id=self.SELLER,
            status=status, sales=sales,
        )

    def _run(self, outcome, ids=None, **kw):
        return GumtreeSyncRun.objects.create(
            user=self.user, profile=self.profile, seller_profile_id=self.SELLER,
            outcome=outcome, returned_list_ids=ids or [], **kw
        )

    def test_counts_reconcile_and_classify_differences(self):
        # Gumtree returned a, b, c. DB has a, b (and z which is gone from Gumtree).
        self._run('success', ids=['a', 'b', 'c'], fetched_count=3)
        self._vl('a')
        self._vl('b', status='pending')
        self._vl('z')  # in DB, not on Gumtree anymore

        data = build_gumtree_reconciliation(self.user)
        s = data['summary']
        self.assertEqual(s['gumtree_total'], 3)
        self.assertEqual(s['matched_in_db'], 2)
        self.assertEqual(s['missing_from_db'], 1)
        self.assertEqual(s['no_longer_on_gumtree'], 1)
        self.assertTrue(s['reconciles'])  # 3 == 2 + 1

        # 'c' was returned by the sync but has no DB row → UNKNOWN (unresolved).
        self.assertEqual(data['missing_from_db'][0]['list_id'], 'c')
        self.assertEqual(data['missing_from_db'][0]['reason'], REASON_UNKNOWN)
        self.assertEqual(s['unresolved'], 1)

        # 'z' is in DB but gone from Gumtree → SOLD_OR_REMOVED.
        self.assertEqual(data['no_longer_on_gumtree'][0]['list_id'], 'z')
        self.assertEqual(data['no_longer_on_gumtree'][0]['reason'], REASON_SOLD_OR_REMOVED)

    def test_failed_last_sync_is_not_treated_as_no_ads(self):
        # Last successful sync saw a, b; a later run FAILED.
        self._run('success', ids=['a', 'b'], fetched_count=2)
        self._run('failed', http_status=402, error_detail='quota')
        self._vl('a')  # 'b' missing from DB

        data = build_gumtree_reconciliation(self.user)
        # Still reports 2 ads from the last successful sync — not 0.
        self.assertEqual(data['summary']['gumtree_total'], 2)
        self.assertEqual(data['sync']['ad_set_source'], 'last_sync')
        self.assertIn('FAILED', (data['sync']['note'] or ''))
        # 'b' missing while the last run failed → SYNC_FAILURE, not UNKNOWN.
        self.assertEqual(data['missing_from_db'][0]['list_id'], 'b')
        self.assertEqual(data['missing_from_db'][0]['reason'], REASON_SYNC_FAILURE)

    def test_no_successful_sync_cannot_compute_totals(self):
        self._run('failed', http_status=402)
        data = build_gumtree_reconciliation(self.user)
        self.assertIsNone(data['summary']['gumtree_total'])
        self.assertEqual(data['sync']['ad_set_source'], 'none')

    def test_live_fetch_adds_not_yet_synced_and_falls_back_on_failure(self):
        # Last successful sync saw a, b. A fresh live check returns a, b, c (c is new).
        self._run('success', ids=['a', 'b'], fetched_count=2)
        self._vl('a'); self._vl('b')

        def live_ok(seller_id):
            return True, {'a', 'b', 'c'}, 200, None
        data = build_gumtree_reconciliation(self.user, live=True, live_fetcher=live_ok)
        self.assertEqual(data['sync']['ad_set_source'], 'live')
        self.assertEqual(data['summary']['gumtree_total'], 3)
        # 'c' is on Gumtree but not in the last successful sync → NOT_YET_SYNCED.
        self.assertEqual(data['missing_from_db'][0]['list_id'], 'c')
        self.assertEqual(data['missing_from_db'][0]['reason'], REASON_NOT_YET_SYNCED)

        # If the live fetch fails, fall back to the last successful sync (never 0 ads).
        def live_fail(seller_id):
            return False, set(), 402, 'quota'
        data2 = build_gumtree_reconciliation(self.user, live=True, live_fetcher=live_fail)
        self.assertEqual(data2['sync']['ad_set_source'], 'last_sync_fallback')
        self.assertEqual(data2['summary']['gumtree_total'], 2)
        self.assertEqual(data2['sync']['live_error'], 'quota')


class MyStatsEndpointTests(TestCase):
    """The dealer self-serve /my-stats/ endpoint returns ONLY the caller's own stats."""
    SELLER = '777'

    def setUp(self):
        self.dealer = User.objects.create_user(email='mine@example.com', password='x')
        self.other = User.objects.create_user(email='other@example.com', password='x')
        GumtreeProfileListing.objects.create(
            user=self.dealer, url='https://g/777', profile_id=self.SELLER, status='completed')
        GumtreeSyncRun.objects.create(
            user=self.dealer, seller_profile_id=self.SELLER, outcome='success',
            returned_list_ids=['a', 'b'], fetched_count=2)
        VehicleListing.objects.create(
            user=self.dealer, list_id='a', seller_profile_id=self.SELLER, status='completed')
        # 2 live FB listings + 1 queued for the dealer.
        FacebookListingSnapshot.objects.create(user=self.dealer, fb_listing_id='fb1', title='A')
        FacebookListingSnapshot.objects.create(user=self.dealer, fb_listing_id='fb2', title='B')
        UnpublishedListingSnapshot.objects.create(
            user=self.dealer, title='C', reason='INSUFFICIENT_IMAGES', images_count=1)
        # The OTHER dealer has its own (more) data that must never leak in.
        for i in range(5):
            FacebookListingSnapshot.objects.create(user=self.other, fb_listing_id=f'o{i}', title='x')

    def test_dealer_sees_only_their_own_stats(self):
        client = APIClient()
        client.force_authenticate(user=self.dealer)
        resp = client.get(reverse('get_my_stats'))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['user_id'], self.dealer.id)
        self.assertEqual(body['facebook']['total_live'], 2)   # NOT 7 (other dealer excluded)
        self.assertEqual(body['queued_count'], 1)
        self.assertEqual(body['gumtree']['summary']['gumtree_total'], 2)

    def test_requires_authentication(self):
        resp = APIClient().get(reverse('get_my_stats'))
        self.assertIn(resp.status_code, (401, 403))
