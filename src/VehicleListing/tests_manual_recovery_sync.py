"""Gumtree sync must treat NEEDS_MANUAL_RECOVERY rows like any other
published car: refresh price/photos when Gumtree changed them, mark the row
sold when the ad is gone, and (through the existing status/sales guards) keep
the extension from publishing a removed car again.

Run: python src/manage.py test VehicleListing.tests_manual_recovery_sync --settings=relister.settings_test
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User

from .gumtree_scraper import NEEDS_MANUAL_RECOVERY, gumtree_profile_listings_thread
from .models import GumtreeProfileListing, Vehicle, VehicleListing

SELLER = 'seller-1'
OLD_IMAGES = ['https://img.example/a.jpg', 'https://img.example/b.jpg']


def _result(**overrides):
    result = dict(
        year='2018', make='Toyota', model='Corolla', variant='Ascent', color='White',
        body_type='Hatchback', fuel_type='Petrol', transmission='Automatic',
        price=15990, mileage=60000, mileage_unavailable=False, description='desc',
        image=list(OLD_IMAGES), url='https://gumtree.example/ad/1', vin=None,
        location='Sydney, NSW',
    )
    result.update(overrides)
    return result


@mock.patch('VehicleListing.gumtree_scraper.get_gumtree_listing_details')
class ManualRecoveryGumtreeSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.profile = GumtreeProfileListing.objects.create(user=self.user, profile_id=SELLER)

    def _listing(self, list_id, status, **kw):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2018', color='White')
        fields = dict(
            user=self.user, vehicle=vehicle, gumtree_profile=self.profile, seller_profile_id=SELLER,
            list_id=list_id, status=status, price='15990', images=list(OLD_IMAGES),
            description='desc', location='Sydney, NSW', url='https://gumtree.example/ad/1',
            listed_on=timezone.now() - timedelta(days=3),
        )
        fields.update(kw)
        return VehicleListing.objects.create(**fields)

    def _sync(self, ids):
        gumtree_profile_listings_thread([{'id': i} for i in ids], self.profile, self.user, SELLER)

    # 1. price change on Gumtree is written to the manual-recovery row
    def test_manual_recovery_row_gets_price_update(self, mock_details):
        row = self._listing('ad-1', NEEDS_MANUAL_RECOVERY)
        mock_details.return_value = _result(price=14990)
        self._sync(['ad-1'])
        row.refresh_from_db()
        self.assertEqual(row.price, '14990')
        self.assertTrue(row.is_changed)
        self.assertEqual(row.status, NEEDS_MANUAL_RECOVERY)  # recovery flow untouched

    # 2. photo change on Gumtree is written to the manual-recovery row
    def test_manual_recovery_row_gets_photo_update(self, mock_details):
        row = self._listing('ad-1', NEEDS_MANUAL_RECOVERY)
        new_images = OLD_IMAGES + ['https://img.example/c.jpg']
        mock_details.return_value = _result(image=new_images)
        self._sync(['ad-1'])
        row.refresh_from_db()
        self.assertEqual(set(row.images), set(new_images))
        self.assertTrue(row.is_changed)
        self.assertEqual(row.status, NEEDS_MANUAL_RECOVERY)

    # 3. nothing changed → row left alone (no spurious is_changed)
    def test_manual_recovery_row_unchanged_is_not_flagged(self, mock_details):
        row = self._listing('ad-1', NEEDS_MANUAL_RECOVERY)
        mock_details.return_value = _result()
        self._sync(['ad-1'])
        row.refresh_from_db()
        self.assertFalse(row.is_changed)
        self.assertEqual(row.status, NEEDS_MANUAL_RECOVERY)

    # 4. ad gone from Gumtree → marked sold/removed
    def test_manual_recovery_row_missing_from_gumtree_is_marked_sold(self, mock_details):
        gone = self._listing('ad-gone', NEEDS_MANUAL_RECOVERY)
        live = self._listing('ad-live', 'completed')
        mock_details.return_value = _result()
        self._sync(['ad-live'])
        gone.refresh_from_db(); live.refresh_from_db()
        self.assertEqual(gone.status, 'sold')
        self.assertTrue(gone.sales)
        self.assertIsNotNone(gone.sold_at)
        self.assertEqual(live.status, 'completed')
        self.assertFalse(live.sales)

    # 5. a failed detail fetch is not treated as "changed" or "removed"
    def test_failed_detail_fetch_leaves_manual_recovery_row_untouched(self, mock_details):
        row = self._listing('ad-1', NEEDS_MANUAL_RECOVERY)
        mock_details.return_value = None
        self._sync(['ad-1'])
        row.refresh_from_db()
        self.assertEqual(row.status, NEEDS_MANUAL_RECOVERY)
        self.assertEqual(row.price, '15990')
        self.assertFalse(row.sales)

    # 6. regression: normal completed rows still sync exactly as before
    def test_completed_row_still_refreshed_and_marked_sold(self, mock_details):
        updated = self._listing('ad-1', 'completed')
        gone = self._listing('ad-2', 'completed')
        pending_gone = self._listing('ad-3', 'pending', listed_on=None,
                                     )
        VehicleListing.objects.filter(pk=pending_gone.pk).update(created_at=timezone.now() - timedelta(days=2))
        mock_details.return_value = _result(price=13990)
        self._sync(['ad-1'])
        updated.refresh_from_db(); gone.refresh_from_db()
        self.assertEqual(updated.price, '13990')
        self.assertEqual(updated.status, 'completed')
        self.assertEqual(gone.status, 'sold')
        self.assertFalse(VehicleListing.objects.filter(pk=pending_gone.pk).exists())  # pending rows are deleted


class ManualRecoveryPublishRestrictionTests(TestCase):
    """Once a manual-recovery car is marked sold, every endpoint the extension
    publishes from must expose it as sold (status='sold', sales=True) or omit it."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.profile = GumtreeProfileListing.objects.create(
            user=self.user, profile_id=SELLER, url='https://www.gumtree.com.au/sellerprofile/1', status='completed')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _sold_recovery_row(self):
        row = VehicleListing.objects.create(
            user=self.user, gumtree_profile=self.profile, seller_profile_id=SELLER, list_id='ad-gone',
            status=NEEDS_MANUAL_RECOVERY, images=list(OLD_IMAGES), listed_on=timezone.now() - timedelta(days=3))
        with mock.patch('VehicleListing.gumtree_scraper.get_gumtree_listing_details', return_value=_result()):
            gumtree_profile_listings_thread([{'id': 'ad-other'}], self.profile, self.user, SELLER)
        row.refresh_from_db()
        return row

    def test_gumtree_listings_endpoint_reports_removed_car_as_sold(self):
        row = self._sold_recovery_row()
        resp = self.client.get(reverse('get_user_gumtree_profile_vehicle_listings'), {'url': self.profile.url})
        self.assertEqual(resp.status_code, 200)
        mine = [r for r in resp.json()['results'] if r['id'] == row.id][0]
        # The extension drops status 'sold' (PUBLISHABLE_STATUSES) and GUARD 1 blocks sales=True.
        self.assertEqual(mine['status'], 'sold')
        self.assertTrue(mine['sales'])

    def test_old_listings_relist_queue_excludes_removed_car(self):
        row = self._sold_recovery_row()
        resp = self.client.get(reverse('get_old_vehicle_listings'), {'days': 0})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(row.id, [r['id'] for r in resp.json()['results']])

    def test_live_verify_marks_manual_recovery_car_sold_when_gone(self):
        row = VehicleListing.objects.create(
            user=self.user, gumtree_profile=self.profile, seller_profile_id=SELLER, list_id='ad-x',
            status=NEEDS_MANUAL_RECOVERY)
        with mock.patch('VehicleListing.views.is_gumtree_listing_active', return_value=False):
            resp = self.client.get(reverse('verify_listing_active'), {'id': row.id})
        self.assertEqual(resp.json(), {'success': True, 'active': False, 'indeterminate': False})
        row.refresh_from_db()
        self.assertEqual(row.status, 'sold')
        self.assertTrue(row.sales)
