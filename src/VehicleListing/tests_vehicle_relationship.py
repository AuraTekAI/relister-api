"""Vehicle ↔ VehicleListing relationship regression tests.

Covers the production incident where a request queried VehicleListing but the
required spec data lived in Vehicle and the relationship was never followed:
listings were created with vehicle=NULL (nothing ever built Vehicle rows) and
serializers read only the listing's own denormalized columns, so data placed
on the Vehicle side was invisible to the API.

The core regression test (`test_api_returns_vehicle_data_when_it_differs_from_
listing_columns`) FAILS on the old implementation — which served the stale
listing column — and passes now that reads resolve through the relationship.

Run with:
    python src/manage.py test VehicleListing.tests_vehicle_relationship --settings=relister.settings_test
"""
from unittest import mock

from django.test import TestCase

from accounts.models import User

from .gumtree_scraper import gumtree_profile_listings_thread
from .models import GumtreeProfileListing, Vehicle, VehicleListing
from .serializers import (
    ProductDetailSerializer,
    ProductListSerializer,
    VehicleListingSerializer,
)
from .vehicle_sync import backfill_vehicles, sync_vehicle_for_listing

REAL_VIN = "1HGCM82633A123456"
OTHER_VIN = "JT2BF22K1W0123456"


def make_listing(user, **overrides):
    defaults = dict(
        seller_profile_id='seller-1', list_id=overrides.pop('list_id', 'ad-1'),
        make='Mazda', model='MAZDA3 NEO', variant='Neo', year='2013',
        color='White', body_type='Hatchback', fuel_type='Petrol',
        transmission='Automatic', mileage=50000, price='12990',
        status='completed', images=[],
    )
    defaults.update(overrides)
    return VehicleListing.objects.create(user=user, **defaults)


class VehicleSpecSourcingSerializerTests(TestCase):
    """Reads must follow VehicleListing → Vehicle, with a fallback for legacy
    unlinked rows."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')

    def test_api_returns_vehicle_data_when_it_differs_from_listing_columns(self):
        # THE regression: the canonical data lives on Vehicle; the listing's
        # denormalized copy is stale. The old implementation returned the
        # stale listing column ('WRONGMAKE') because it never joined Vehicle.
        vehicle = Vehicle.objects.create(
            make='Toyota', model='Landcruiser', year='2004', color='Silver',
            mileage=417000, transmission='Manual', fuel_type='Diesel',
            body_type='SUV', variant='GXL', vin=REAL_VIN,
        )
        listing = make_listing(
            self.user, vehicle=vehicle,
            make='WRONGMAKE', model='WRONGMODEL', year='1999',
            color='WRONGCOLOR', mileage=1,
        )

        data = VehicleListingSerializer(listing).data
        self.assertEqual(data['make'], 'Toyota')
        self.assertEqual(data['model'], 'Landcruiser')
        self.assertEqual(data['year'], '2004')
        self.assertEqual(data['color'], 'Silver')
        self.assertEqual(data['mileage'], 417000)
        self.assertEqual(data['vin'], REAL_VIN)

    def test_unlinked_legacy_listing_falls_back_to_its_own_columns(self):
        listing = make_listing(self.user, vehicle=None)
        data = VehicleListingSerializer(listing).data
        self.assertEqual(data['make'], 'Mazda')
        self.assertEqual(data['mileage'], 50000)

    def test_vehicle_field_missing_a_value_falls_back_per_field(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020', color=None)
        listing = make_listing(self.user, vehicle=vehicle, color='Blue')
        data = VehicleListingSerializer(listing).data
        self.assertEqual(data['make'], 'Toyota')   # from Vehicle
        self.assertEqual(data['color'], 'Blue')    # Vehicle has none -> listing's

    def test_updating_vehicle_is_reflected_in_listing_reads(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020')
        listing = make_listing(self.user, vehicle=vehicle)
        vehicle.color = 'Red'
        vehicle.save()
        listing.refresh_from_db()
        self.assertEqual(VehicleListingSerializer(listing).data['color'], 'Red')

    def test_storefront_serializers_follow_the_relationship_too(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020', mileage=1000)
        listing = make_listing(self.user, vehicle=vehicle, is_listed=True,
                               make='WRONG', model='WRONG', year='1999')
        list_data = ProductListSerializer(listing).data
        detail_data = ProductDetailSerializer(listing).data
        self.assertEqual(list_data['make'], 'Toyota')
        self.assertEqual(detail_data['model'], 'Corolla')
        self.assertEqual(list_data['name'], '2020 Toyota Corolla')
        self.assertEqual(detail_data['name'], '2020 Toyota Corolla')

    def test_multiple_listings_share_one_vehicles_data(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020')
        a = make_listing(self.user, vehicle=vehicle, list_id='ad-a', make='STALE-A')
        b = make_listing(self.user, vehicle=vehicle, list_id='ad-b', make='STALE-B')
        self.assertEqual(vehicle.listings.count(), 2)
        self.assertEqual(VehicleListingSerializer(a).data['make'], 'Toyota')
        self.assertEqual(VehicleListingSerializer(b).data['make'], 'Toyota')


class VehicleSyncTests(TestCase):
    """sync_vehicle_for_listing: creation, dealer-scoped reuse, propagation."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')

    def test_creates_and_links_vehicle_from_listing(self):
        listing = make_listing(self.user, vin=REAL_VIN)
        vehicle = sync_vehicle_for_listing(listing)
        listing.refresh_from_db()
        self.assertIsNotNone(vehicle)
        self.assertEqual(listing.vehicle_id, vehicle.id)
        self.assertEqual(vehicle.make, 'Mazda')
        self.assertEqual(vehicle.vin, REAL_VIN)

    def test_same_dealer_same_vin_reuses_the_vehicle(self):
        first = make_listing(self.user, list_id='ad-a', vin=REAL_VIN)
        v1 = sync_vehicle_for_listing(first)
        second = make_listing(self.user, list_id='ad-b', vin=REAL_VIN,
                              color='Grey')  # cosmetic drift, same car
        v2 = sync_vehicle_for_listing(second)
        self.assertEqual(v1.id, v2.id)
        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertEqual(v1.listings.count(), 2)

    def test_different_dealers_never_share_a_vehicle_even_on_vin(self):
        other = User.objects.create_user(email='other@test.invalid', password='x')
        v1 = sync_vehicle_for_listing(make_listing(self.user, vin=REAL_VIN))
        v2 = sync_vehicle_for_listing(
            make_listing(other, seller_profile_id='seller-2', vin=REAL_VIN))
        self.assertNotEqual(v1.id, v2.id)

    def test_structural_match_reuses_vehicle_without_vin(self):
        v1 = sync_vehicle_for_listing(make_listing(self.user, list_id='ad-a'))
        v2 = sync_vehicle_for_listing(make_listing(self.user, list_id='ad-b'))
        self.assertEqual(v1.id, v2.id)

    def test_ambiguous_structural_match_creates_a_new_vehicle(self):
        # Two distinct vehicles already share make/model/year/color — a third
        # listing matching both must NOT guess; it gets its own Vehicle.
        sync_vehicle_for_listing(make_listing(self.user, list_id='ad-a', mileage=40000))
        sync_vehicle_for_listing(
            make_listing(self.user, list_id='ad-b', mileage=42000, vin=OTHER_VIN))
        # ad-a and ad-b structurally merge? ad-b carries a VIN but same
        # structure; force distinct vehicles for the ambiguity setup:
        b = VehicleListing.objects.get(list_id='ad-b')
        if b.vehicle_id == VehicleListing.objects.get(list_id='ad-a').vehicle_id:
            fresh = Vehicle.objects.create(make='Mazda', model='MAZDA3 NEO',
                                           year='2013', color='White', vin=OTHER_VIN)
            b.vehicle = fresh
            b.save(update_fields=['vehicle'])
        third = make_listing(self.user, list_id='ad-c', mileage=None, vin=None)
        v3 = sync_vehicle_for_listing(third)
        self.assertNotIn(v3.id, [
            VehicleListing.objects.get(list_id='ad-a').vehicle_id])
        self.assertEqual(v3.listings.count(), 1)

    def test_update_propagates_listing_changes_to_vehicle(self):
        listing = make_listing(self.user)
        vehicle = sync_vehicle_for_listing(listing)
        listing.mileage = 51500
        listing.color = 'Grey'
        listing.save()
        sync_vehicle_for_listing(listing)
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.mileage, 51500)
        self.assertEqual(vehicle.color, 'Grey')

    def test_sync_never_raises(self):
        listing = make_listing(self.user)
        with mock.patch('VehicleListing.vehicle_sync._find_matching_vehicle',
                        side_effect=RuntimeError('boom')):
            self.assertIsNone(sync_vehicle_for_listing(listing))  # swallowed, logged

    def test_deleting_vehicle_keeps_the_listing(self):
        listing = make_listing(self.user)
        vehicle = sync_vehicle_for_listing(listing)
        vehicle.delete()
        listing.refresh_from_db()  # would raise if the listing was cascaded away
        self.assertIsNone(listing.vehicle_id)


class ScraperCreatesVehicleTests(TestCase):
    """End-to-end through the real Gumtree pipeline (network mocked): a brand
    new scrape must come out the other side with a linked Vehicle."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.profile = GumtreeProfileListing.objects.create(user=self.user, profile_id='seller-1')

    def _result(self, **overrides):
        result = dict(
            year='2013', make='Mazda', model='MAZDA3 NEO', variant='Neo', color='White',
            body_type='Hatchback', fuel_type='Petrol', transmission='Automatic',
            price=12990, mileage=50100, mileage_unavailable=False, description='desc',
            image=[], url='https://gumtree.example/ad/999', vin=REAL_VIN,
        )
        result.update(overrides)
        return result

    @mock.patch('VehicleListing.gumtree_scraper.get_gumtree_listing_details')
    def test_new_scraped_listing_gets_a_linked_vehicle(self, mock_details):
        mock_details.return_value = self._result()
        gumtree_profile_listings_thread([{'id': 'ad-1'}], self.profile, self.user, 'seller-1')

        listing = VehicleListing.objects.get(user=self.user, list_id='ad-1')
        self.assertIsNotNone(listing.vehicle, "scraper must create+link a Vehicle")
        self.assertEqual(listing.vehicle.make, 'Mazda')
        self.assertEqual(listing.vehicle.vin, REAL_VIN)
        self.assertEqual(listing.vehicle.mileage, 50100)

    @mock.patch('VehicleListing.gumtree_scraper.get_gumtree_listing_details')
    def test_refresh_of_stale_listing_updates_the_vehicle(self, mock_details):
        listing = make_listing(self.user, list_id='ad-1', gumtree_profile=self.profile)
        vehicle = sync_vehicle_for_listing(listing)
        # Make it eligible for the refresh branch (old + pending).
        VehicleListing.objects.filter(pk=listing.pk).update(
            status='pending', created_at=listing.created_at.replace(year=2020))
        mock_details.return_value = self._result(price=11990, mileage=52000, color='Grey', vin=None)

        gumtree_profile_listings_thread([{'id': 'ad-1'}], self.profile, self.user, 'seller-1')

        vehicle.refresh_from_db()
        self.assertEqual(vehicle.mileage, 52000)
        self.assertEqual(vehicle.color, 'Grey')


class BackfillTests(TestCase):
    """Rows created before the sync existed get linked, deduped per dealer."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')

    def test_backfill_links_and_dedupes_by_vin(self):
        make_listing(self.user, list_id='ad-a', vin=REAL_VIN)
        make_listing(self.user, list_id='ad-b', vin=REAL_VIN, color='Grey')
        make_listing(self.user, list_id='ad-c', vin=OTHER_VIN,
                     make='Toyota', model='Corolla', year='2020')

        linked, created = backfill_vehicles()

        self.assertEqual(linked, 3)
        self.assertEqual(Vehicle.objects.count(), 2)
        self.assertEqual(
            VehicleListing.objects.filter(vehicle__isnull=True).count(), 0)
        a = VehicleListing.objects.get(list_id='ad-a')
        b = VehicleListing.objects.get(list_id='ad-b')
        self.assertEqual(a.vehicle_id, b.vehicle_id)

    def test_backfill_is_idempotent(self):
        make_listing(self.user, vin=REAL_VIN)
        backfill_vehicles()
        linked_again, created_again = backfill_vehicles()
        self.assertEqual((linked_again, created_again), (0, 0))
        self.assertEqual(Vehicle.objects.count(), 1)

    def test_backfill_never_links_across_dealers(self):
        other = User.objects.create_user(email='other@test.invalid', password='x')
        make_listing(self.user, vin=REAL_VIN)
        make_listing(other, seller_profile_id='seller-2', vin=REAL_VIN)
        backfill_vehicles()
        self.assertEqual(Vehicle.objects.count(), 2)
