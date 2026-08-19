"""Vehicle ↔ VehicleListing relationship regression tests.

Covers the production incident where a request queried VehicleListing but the
required spec data lived in Vehicle and the relationship was never followed.
Since migration 0056 the duplicated spec columns are GONE from VehicleListing —
Vehicle is the only place spec data exists — so these tests pin down:

  * serializers emit the same spec keys as before, fed from the Vehicle row;
  * `listing.make`-style attribute reads delegate to the Vehicle (read-only);
  * sync_vehicle_for_listing(listing, spec) creates/links/refreshes Vehicles
    with conservative dealer-scoped matching;
  * the scrapers come out the other side with a linked Vehicle.

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
from .vehicle_sync import VEHICLE_SPEC_FIELDS, sync_vehicle_for_listing

REAL_VIN = "1HGCM82633A123456"
OTHER_VIN = "JT2BF22K1W0123456"


def make_spec(**overrides):
    spec = dict(
        make='Mazda', model='MAZDA3 NEO', variant='Neo', year='2013',
        color='White', body_type='Hatchback', fuel_type='Petrol',
        transmission='Automatic', mileage=50000, vin=None,
    )
    spec.update(overrides)
    return spec


def make_listing(user, vehicle=None, **overrides):
    defaults = dict(
        seller_profile_id='seller-1', list_id=overrides.pop('list_id', 'ad-1'),
        price='12990', status='completed', images=[],
    )
    defaults.update(overrides)
    return VehicleListing.objects.create(user=user, vehicle=vehicle, **defaults)


class VehicleSpecSourcingSerializerTests(TestCase):
    """Spec keys in API output must come from the linked Vehicle row and the
    output SHAPE must be unchanged from the pre-0056 responses."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')

    def test_api_serves_the_vehicle_rows_data(self):
        vehicle = Vehicle.objects.create(
            make='Toyota', model='Landcruiser', year='2004', color='Silver',
            mileage=417000, transmission='Manual', fuel_type='Diesel',
            body_type='SUV', variant='GXL', vin=REAL_VIN,
        )
        listing = make_listing(self.user, vehicle=vehicle)

        data = VehicleListingSerializer(listing).data
        self.assertEqual(data['make'], 'Toyota')
        self.assertEqual(data['model'], 'Landcruiser')
        self.assertEqual(data['year'], '2004')
        self.assertEqual(data['color'], 'Silver')
        self.assertEqual(data['mileage'], 417000)
        self.assertEqual(data['vin'], REAL_VIN)

    def test_response_shape_keeps_every_spec_key_even_when_unlinked(self):
        # An unlinked row shouldn't exist post-backfill, but if one does the
        # extension must still receive the same keys (as null), not a KeyError.
        listing = make_listing(self.user, vehicle=None)
        data = VehicleListingSerializer(listing).data
        for field in VEHICLE_SPEC_FIELDS:
            self.assertIn(field, data)
            self.assertIsNone(data[field])

    def test_updating_vehicle_is_reflected_in_listing_reads(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020')
        listing = make_listing(self.user, vehicle=vehicle)
        vehicle.color = 'Red'
        vehicle.save()
        listing.refresh_from_db()
        self.assertEqual(VehicleListingSerializer(listing).data['color'], 'Red')

    def test_attribute_reads_delegate_to_the_vehicle(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020', mileage=1000)
        listing = make_listing(self.user, vehicle=vehicle)
        self.assertEqual(listing.make, 'Toyota')
        self.assertEqual(listing.mileage, 1000)
        self.assertEqual(str(listing), '2020 Toyota Corolla')

    def test_spec_attributes_are_read_only_on_the_listing(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020')
        listing = make_listing(self.user, vehicle=vehicle)
        with self.assertRaises(AttributeError):
            listing.make = 'Mazda'  # writes must go through vehicle_sync

    def test_storefront_serializers_follow_the_relationship_too(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020', mileage=1000)
        listing = make_listing(self.user, vehicle=vehicle, is_listed=True)
        list_data = ProductListSerializer(listing).data
        detail_data = ProductDetailSerializer(listing).data
        self.assertEqual(list_data['make'], 'Toyota')
        self.assertEqual(detail_data['model'], 'Corolla')
        self.assertEqual(list_data['name'], '2020 Toyota Corolla')
        self.assertEqual(detail_data['name'], '2020 Toyota Corolla')
        # Shape pinned: the storefront grid keeps its spec keys.
        for key in ('year', 'body_type', 'fuel_type', 'variant', 'make',
                    'model', 'mileage', 'transmission', 'color'):
            self.assertIn(key, list_data)

    def test_multiple_listings_share_one_vehicles_data(self):
        vehicle = Vehicle.objects.create(make='Toyota', model='Corolla', year='2020')
        a = make_listing(self.user, vehicle=vehicle, list_id='ad-a')
        b = make_listing(self.user, vehicle=vehicle, list_id='ad-b')
        self.assertEqual(vehicle.listings.count(), 2)
        self.assertEqual(VehicleListingSerializer(a).data['make'], 'Toyota')
        self.assertEqual(VehicleListingSerializer(b).data['make'], 'Toyota')


class VehicleSyncTests(TestCase):
    """sync_vehicle_for_listing: creation, dealer-scoped reuse, propagation."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')

    def test_creates_and_links_vehicle_from_spec(self):
        listing = make_listing(self.user)
        vehicle = sync_vehicle_for_listing(listing, make_spec(vin=REAL_VIN))
        listing.refresh_from_db()
        self.assertIsNotNone(vehicle)
        self.assertEqual(listing.vehicle_id, vehicle.id)
        self.assertEqual(vehicle.make, 'Mazda')
        self.assertEqual(vehicle.vin, REAL_VIN)

    def test_same_dealer_same_vin_reuses_the_vehicle(self):
        v1 = sync_vehicle_for_listing(
            make_listing(self.user, list_id='ad-a'), make_spec(vin=REAL_VIN))
        v2 = sync_vehicle_for_listing(
            make_listing(self.user, list_id='ad-b'),
            make_spec(vin=REAL_VIN, color='Grey'))  # cosmetic drift, same car
        self.assertEqual(v1.id, v2.id)
        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertEqual(v1.listings.count(), 2)

    def test_different_dealers_never_share_a_vehicle_even_on_vin(self):
        other = User.objects.create_user(email='other@test.invalid', password='x')
        v1 = sync_vehicle_for_listing(make_listing(self.user), make_spec(vin=REAL_VIN))
        v2 = sync_vehicle_for_listing(
            make_listing(other, seller_profile_id='seller-2'), make_spec(vin=REAL_VIN))
        self.assertNotEqual(v1.id, v2.id)

    def test_structural_match_reuses_vehicle_without_vin(self):
        v1 = sync_vehicle_for_listing(make_listing(self.user, list_id='ad-a'), make_spec())
        v2 = sync_vehicle_for_listing(make_listing(self.user, list_id='ad-b'), make_spec())
        self.assertEqual(v1.id, v2.id)

    def test_ambiguous_structural_match_creates_a_new_vehicle(self):
        # Two distinct vehicles already share make/model/year/color — a third
        # listing matching both must NOT guess; it gets its own Vehicle.
        a = Vehicle.objects.create(**make_spec(mileage=40000))
        b = Vehicle.objects.create(**make_spec(mileage=42000, vin=OTHER_VIN))
        make_listing(self.user, vehicle=a, list_id='ad-a')
        make_listing(self.user, vehicle=b, list_id='ad-b')
        third = make_listing(self.user, list_id='ad-c')
        v3 = sync_vehicle_for_listing(third, make_spec(mileage=None))
        self.assertNotIn(v3.id, [a.id, b.id])
        self.assertEqual(v3.listings.count(), 1)

    def test_update_propagates_fresh_spec_to_vehicle(self):
        listing = make_listing(self.user)
        vehicle = sync_vehicle_for_listing(listing, make_spec())
        sync_vehicle_for_listing(listing, make_spec(mileage=51500, color='Grey'))
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.mileage, 51500)
        self.assertEqual(vehicle.color, 'Grey')

    def test_sync_never_raises(self):
        listing = make_listing(self.user)
        with mock.patch('VehicleListing.vehicle_sync._find_matching_vehicle',
                        side_effect=RuntimeError('boom')):
            self.assertIsNone(sync_vehicle_for_listing(listing, make_spec()))  # swallowed, logged

    def test_deleting_vehicle_keeps_the_listing(self):
        listing = make_listing(self.user)
        vehicle = sync_vehicle_for_listing(listing, make_spec())
        vehicle.delete()
        listing.refresh_from_db()  # would raise if the listing was cascaded away
        self.assertIsNone(listing.vehicle_id)
        self.assertIsNone(listing.make)  # delegate degrades to None, not a crash


class ScraperCreatesVehicleTests(TestCase):
    """End-to-end through the real Gumtree pipeline (network mocked): a brand
    new scrape must come out the other side with a linked Vehicle carrying the
    parsed spec."""

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
        # The ad-side fields still land on the listing itself.
        self.assertEqual(listing.price, '12990')

    @mock.patch('VehicleListing.gumtree_scraper.get_gumtree_listing_details')
    def test_refresh_of_stale_listing_updates_the_vehicle(self, mock_details):
        listing = make_listing(self.user, list_id='ad-1', gumtree_profile=self.profile)
        vehicle = sync_vehicle_for_listing(listing, make_spec())
        # Make it eligible for the refresh branch (old + pending).
        VehicleListing.objects.filter(pk=listing.pk).update(
            status='pending', created_at=listing.created_at.replace(year=2020))
        mock_details.return_value = self._result(price=11990, mileage=52000, color='Grey', vin=None)

        gumtree_profile_listings_thread([{'id': 'ad-1'}], self.profile, self.user, 'seller-1')

        vehicle.refresh_from_db()
        self.assertEqual(vehicle.mileage, 52000)
        self.assertEqual(vehicle.color, 'Grey')
