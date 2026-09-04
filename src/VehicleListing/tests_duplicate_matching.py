"""Duplicate-vehicle-handling regression tests.

Covers duplicate_matching.py directly (no DB), the two live import pipelines
(custom_domain_scraper._process_stock_url, gumtree_scraper.gumtree_profile_listings_thread)
re-matching a vehicle whose SOURCE listing id changed instead of creating a
second row, and the merge_duplicate_vehicle_listings cleanup command.

Run with:
    python src/manage.py test VehicleListing.tests_duplicate_matching --settings=relister.settings_test
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from accounts.models import User

from .custom_domain_scraper import _process_stock_url
from .duplicate_matching import find_existing_vehicle, is_valid_vin, normalize_for_matching
from .gumtree_scraper import gumtree_profile_listings_thread
from .models import (
    CustomDomainProfileListing,
    FacebookListing,
    GumtreeProfileListing,
    Vehicle,
    VehicleListing,
    VehicleListingImage,
)


REAL_VIN = "1HGCM82633A123456"

# Spec attributes live on Vehicle since migration 0056 — test rows are built
# as a Vehicle + a listing linked to it (mirroring what vehicle_sync produces).
SPEC_FIELDS = ('vin', 'make', 'model', 'year', 'mileage',
               'transmission', 'fuel_type', 'body_type', 'color', 'variant')


def create_listing_with_vehicle(**kwargs):
    spec = {f: kwargs.pop(f) for f in SPEC_FIELDS if f in kwargs}
    vehicle = Vehicle.objects.create(**spec)
    return VehicleListing.objects.create(vehicle=vehicle, **kwargs)


class ValidVinTests(SimpleTestCase):
    def test_real_looking_vin_is_valid(self):
        self.assertTrue(is_valid_vin(REAL_VIN))

    def test_lowercase_and_whitespace_tolerated(self):
        self.assertTrue(is_valid_vin(f"  {REAL_VIN.lower()}  "))

    def test_none_and_empty_are_invalid(self):
        self.assertFalse(is_valid_vin(None))
        self.assertFalse(is_valid_vin(''))

    def test_wrong_length_is_invalid(self):
        self.assertFalse(is_valid_vin("SHORT123"))

    def test_placeholder_junk_is_invalid(self):
        for junk in ("N/A", "-", "0000000000000000", "TBATBATBATBATBAT", "AAAAAAAAAAAAAAAAA"):
            self.assertFalse(is_valid_vin(junk), f"{junk!r} should not be treated as a real VIN")

    def test_non_alnum_is_invalid(self):
        self.assertFalse(is_valid_vin("1HGCM826-3A12345#"))


class NormalizeForMatchingTests(SimpleTestCase):
    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(normalize_for_matching(" mazda3   neo "), normalize_for_matching("MAZDA3 NEO"))

    def test_none_and_empty(self):
        self.assertEqual(normalize_for_matching(None), '')
        self.assertEqual(normalize_for_matching(''), '')


class FindExistingVehicleTests(TestCase):
    """Unit tests against a real (sqlite) queryset — no scraper involved."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')

    def _car(self, **kwargs):
        self._car_counter = getattr(self, '_car_counter', 0) + 1
        defaults = dict(
            user=self.user, seller_profile_id='dealer.example.com', list_id=f'listing-{self._car_counter}',
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White',
            price='12990', mileage=50000,
        )
        defaults.update(kwargs)
        return create_listing_with_vehicle(**defaults)

    def dealer_qs(self):
        return VehicleListing.objects.filter(user=self.user, seller_profile_id='dealer.example.com')

    def test_vin_match_wins_even_with_every_other_field_different(self):
        existing = self._car(vin=REAL_VIN, price='12990', color='White', mileage=50000)
        found = find_existing_vehicle(
            self.dealer_qs(), vin=REAL_VIN,
            make='MAZDA', model='Mazda3 Neo', year='2013', color='Red',  # colour "changed" — repaint/scrape noise
            mileage=52000,
        )
        self.assertEqual(found.pk, existing.pk)

    def test_structural_match_when_unambiguous(self):
        existing = self._car(vin=None)
        found = find_existing_vehicle(
            self.dealer_qs(), vin=None,
            make='Mazda', model='MAZDA3 NEO', variant='Neo', year='2013', color='White', mileage=50100,
        )
        self.assertEqual(found.pk, existing.pk)

    def test_price_change_does_not_prevent_a_match(self):
        existing = self._car(vin=None, price='12990')
        found = find_existing_vehicle(
            self.dealer_qs(), vin=None,
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White',
        )
        self.assertEqual(found.pk, existing.pk)

    def test_two_identical_cars_are_not_auto_merged(self):
        """The explicit false-positive case from the task: same make/model/
        colour but two DIFFERENT physical cars. Ambiguous -> no match."""
        self._car(vin=None, mileage=40000)
        self._car(vin=None, mileage=41000)
        found = find_existing_vehicle(
            self.dealer_qs(), vin=None,
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White', mileage=None,
        )
        self.assertIsNone(found)

    def test_mileage_disambiguates_when_one_candidate_is_clearly_closer(self):
        close = self._car(vin=None, mileage=50000)
        self._car(vin=None, mileage=90000)
        found = find_existing_vehicle(
            self.dealer_qs(), vin=None,
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White', mileage=50200,
        )
        self.assertEqual(found.pk, close.pk)

    def test_different_year_is_a_different_vehicle(self):
        self._car(vin=None, year='2013')
        found = find_existing_vehicle(
            self.dealer_qs(), vin=None,
            make='Mazda', model='MAZDA3 NEO', year='2016', color='White',
        )
        self.assertIsNone(found)

    def test_never_matches_across_dealers(self):
        other_user = User.objects.create_user(email='other@test.invalid', password='x')
        create_listing_with_vehicle(
            user=other_user, seller_profile_id='dealer.example.com', list_id='X',
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White', vin=REAL_VIN,
        )
        # Same VIN, but querying THIS dealer's queryset (self.user) — the other
        # user's row must never be visible/matchable from here.
        found = find_existing_vehicle(self.dealer_qs(), vin=REAL_VIN, make='Mazda', model='MAZDA3 NEO', year='2013')
        self.assertIsNone(found)

    def test_no_data_returns_none_not_a_crash(self):
        self.assertIsNone(find_existing_vehicle(self.dealer_qs()))


class CustomDomainDuplicatePreventionTests(TestCase):
    """The exact scenario from the bug report: a dealer-site stock token
    changes between syncs for the same physical vehicle."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.profile = CustomDomainProfileListing.objects.create(
            user=self.user, url='https://dealer.example.com', profile_id='dealer.example.com',
        )

    def _adapter(self, **result_overrides):
        result = dict(
            list_id='new-token', title='2013 Mazda MAZDA3 Neo', price=12990, description='desc',
            image=[], location=None, body_type='Hatchback', fuel_type='Petrol', color='White',
            variant='Neo', year='2013', model='MAZDA3 NEO', make='Mazda', mileage=50100,
            transmission='Automatic', url='https://dealer.example.com/buy/new-token',
        )
        result.update(result_overrides)
        adapter = mock.Mock()
        adapter.parse_listing.return_value = result
        return adapter

    def test_relisted_vehicle_with_new_token_updates_existing_row_not_a_new_one(self):
        existing = create_listing_with_vehicle(
            user=self.user, seller_profile_id='dealer.example.com', list_id='old-token',
            custom_domain_profile=self.profile,
            make='Mazda', model='MAZDA3 NEO', variant='Neo', year='2013', color='White',
            price='12990', mileage=50000, status='completed',
        )

        adapter = self._adapter(price=11990, mileage=50100)  # price dropped, mileage crept up
        ok = _process_stock_url(
            'https://dealer.example.com/buy/new-token', 'new-token',
            self.profile, self.user, 'dealer.example.com', adapter,
        )

        self.assertTrue(ok)
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 1, "must not have created a second row")
        existing.refresh_from_db()
        self.assertEqual(existing.list_id, 'new-token')
        self.assertEqual(existing.price, '11990')
        self.assertEqual(existing.mileage, 50100)

    def test_genuinely_new_vehicle_still_creates_a_row(self):
        adapter = self._adapter(make='Toyota', model='Corolla', year='2020', color='Blue', mileage=1000)
        ok = _process_stock_url(
            'https://dealer.example.com/buy/new-token', 'new-token',
            self.profile, self.user, 'dealer.example.com', adapter,
        )
        self.assertTrue(ok)
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 1)
        self.assertEqual(VehicleListing.objects.get(user=self.user).make, 'Toyota')

    def test_reappearing_after_being_marked_sold_is_reactivated(self):
        existing = create_listing_with_vehicle(
            user=self.user, seller_profile_id='dealer.example.com', list_id='old-token',
            custom_domain_profile=self.profile,
            make='Mazda', model='MAZDA3 NEO', variant='Neo', year='2013', color='White',
            price='12990', mileage=50000, status='sold', sales=True,
        )

        adapter = self._adapter()
        _process_stock_url(
            'https://dealer.example.com/buy/new-token', 'new-token',
            self.profile, self.user, 'dealer.example.com', adapter,
        )

        existing.refresh_from_db()
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 1)
        self.assertFalse(existing.sales)
        self.assertNotEqual(existing.status, 'sold')


class GumtreeDuplicatePreventionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.profile = GumtreeProfileListing.objects.create(user=self.user, profile_id='seller-1')

    def _result(self, **overrides):
        result = dict(
            year='2013', make='Mazda', model='MAZDA3 NEO', variant='Neo', color='White',
            body_type='Hatchback', fuel_type='Petrol', transmission='Automatic',
            price=12990, mileage=50100, mileage_unavailable=False, description='desc',
            image=[], url='https://gumtree.example/ad/999', vin=None,
        )
        result.update(overrides)
        return result

    @mock.patch('VehicleListing.gumtree_scraper.get_gumtree_listing_details')
    def test_relisted_ad_with_new_id_updates_existing_row_not_a_new_one(self, mock_details):
        existing = create_listing_with_vehicle(
            user=self.user, seller_profile_id='seller-1', list_id='old-ad-id',
            gumtree_profile=self.profile,
            make='Mazda', model='MAZDA3 NEO', variant='Neo', year='2013', color='White',
            price='12990', mileage=50000, status='completed',
        )
        mock_details.return_value = self._result(price=11990, mileage=50100)

        gumtree_profile_listings_thread(
            [{'id': 'new-ad-id'}], self.profile, self.user, 'seller-1',
        )

        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.list_id, 'new-ad-id')
        self.assertEqual(existing.price, '11990')
        self.assertTrue(existing.is_changed)

    @mock.patch('VehicleListing.gumtree_scraper.get_gumtree_listing_details')
    def test_genuinely_new_ad_still_creates_a_row(self, mock_details):
        mock_details.return_value = self._result(make='Toyota', model='Corolla', year='2020', color='Blue')

        gumtree_profile_listings_thread(
            [{'id': 'brand-new-ad'}], self.profile, self.user, 'seller-1',
        )

        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 1)
        self.assertEqual(VehicleListing.objects.get(user=self.user).make, 'Toyota')

    @mock.patch('VehicleListing.gumtree_scraper.get_gumtree_listing_details')
    def test_two_ambiguous_candidates_create_a_new_row_rather_than_guess(self, mock_details):
        create_listing_with_vehicle(
            user=self.user, seller_profile_id='seller-1', list_id='ad-a',
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White', mileage=40000,
        )
        create_listing_with_vehicle(
            user=self.user, seller_profile_id='seller-1', list_id='ad-b',
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White', mileage=42000,
        )
        mock_details.return_value = self._result(mileage=None, variant=None)

        gumtree_profile_listings_thread(
            [{'id': 'ad-c'}], self.profile, self.user, 'seller-1',
        )

        # Ambiguous match correctly refused -> a third row for a genuinely
        # undecidable case, never a silent wrong merge.
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 3)


class MergeDuplicateVehicleListingsCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')

    def test_dry_run_changes_nothing(self):
        create_listing_with_vehicle(
            user=self.user, seller_profile_id='dealer.example.com', list_id='a', vin=REAL_VIN,
            make='Mazda', model='MAZDA3 NEO', year='2013',
        )
        create_listing_with_vehicle(
            user=self.user, seller_profile_id='dealer.example.com', list_id='b', vin=REAL_VIN,
            make='Mazda', model='MAZDA3 NEO', year='2013',
        )
        out = StringIO()
        call_command('merge_duplicate_vehicle_listings', stdout=out)
        self.assertEqual(VehicleListing.objects.count(), 2)
        self.assertIn('Would merge', out.getvalue())

    def test_apply_merges_vin_duplicates_and_repoints_relations(self):
        older = create_listing_with_vehicle(
            user=self.user, seller_profile_id='dealer.example.com', list_id='a', vin=REAL_VIN,
            make='Mazda', model='MAZDA3 NEO', year='2013', facebook_listing_id='fb-123',
        )
        newer = create_listing_with_vehicle(
            user=self.user, seller_profile_id='dealer.example.com', list_id='b', vin=REAL_VIN,
            make='Mazda', model='MAZDA3 NEO', year='2013',
        )
        fb_history = FacebookListing.objects.create(user=self.user, listing=newer, status='pending')
        image = VehicleListingImage.objects.create(listing=newer, source_url='https://example.com/photo.jpg')

        out = StringIO()
        call_command('merge_duplicate_vehicle_listings', '--apply', stdout=out)

        self.assertEqual(VehicleListing.objects.count(), 1)
        survivor = VehicleListing.objects.get()
        # The row with the live Facebook mapping must win, regardless of creation order.
        self.assertEqual(survivor.pk, older.pk)
        self.assertEqual(survivor.facebook_listing_id, 'fb-123')

        fb_history.refresh_from_db()
        self.assertEqual(fb_history.listing_id, survivor.pk)
        image.refresh_from_db()
        self.assertEqual(image.listing_id, survivor.pk)

    def test_ambiguous_group_is_left_untouched(self):
        create_listing_with_vehicle(
            user=self.user, seller_profile_id='dealer.example.com', list_id='a',
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White', mileage=10000,
        )
        create_listing_with_vehicle(
            user=self.user, seller_profile_id='dealer.example.com', list_id='b',
            make='Mazda', model='MAZDA3 NEO', year='2013', color='White', mileage=90000,
        )
        out = StringIO()
        call_command('merge_duplicate_vehicle_listings', '--apply', stdout=out)
        self.assertEqual(VehicleListing.objects.count(), 2, "large mileage gap must not be auto-merged")
        self.assertIn('AMBIGUOUS', out.getvalue())
