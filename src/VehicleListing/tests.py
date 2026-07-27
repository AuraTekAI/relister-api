import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from .models import Vehicle, VehicleListing
from .utils import mark_listing_sold, reactivate_listing, withdraw_listing


def _make_listing(user, **overrides):
    vehicle = Vehicle.objects.create(make="Toyota", model="Corolla", year="2020")
    defaults = {
        "user": user,
        "vehicle": vehicle,
        "price": "15000",
        "status": "completed",
        "seller_profile_id": "seller-1",
    }
    defaults.update(overrides)
    return VehicleListing.objects.create(**defaults)


class VehicleListingLifecycleModelTests(TestCase):
    """Unit tests for the lifecycle fields directly, independent of the HTTP layer."""

    def setUp(self):
        self.user = User.objects.create_user(email="dealer@example.com", password="pw")

    def test_new_listing_defaults_to_active_with_no_lifecycle_timestamps(self):
        listing = _make_listing(self.user)
        self.assertEqual(listing.lifecycle_status, VehicleListing.LIFECYCLE_ACTIVE)
        self.assertIsNone(listing.first_listed_at)
        self.assertIsNone(listing.delisted_at)
        self.assertIsNone(listing.days_to_sell)
        self.assertEqual(listing.relist_count, 0)

    def test_mark_listing_sold_sets_delisted_at_and_computes_days_to_sell(self):
        listing = _make_listing(self.user)
        listing.first_listed_at = timezone.now() - timezone.timedelta(days=10)
        listing.save()

        mark_listing_sold(listing)
        listing.refresh_from_db()

        self.assertEqual(listing.status, "sold")
        self.assertEqual(listing.lifecycle_status, VehicleListing.LIFECYCLE_SOLD)
        self.assertIsNotNone(listing.delisted_at)
        self.assertEqual(listing.days_to_sell, 10)

    def test_mark_listing_sold_without_first_listed_at_leaves_days_to_sell_none(self):
        # Defensive case: a row from before this feature existed, never
        # captured a first_listed_at — must not crash computing a diff.
        listing = _make_listing(self.user)
        mark_listing_sold(listing)
        listing.refresh_from_db()
        self.assertIsNone(listing.days_to_sell)

    def test_withdraw_listing_sets_delisted_at_but_not_days_to_sell(self):
        listing = _make_listing(self.user)
        listing.first_listed_at = timezone.now() - timezone.timedelta(days=5)
        listing.save()

        withdraw_listing(listing)
        listing.refresh_from_db()

        self.assertEqual(listing.status, "withdrawn")
        self.assertEqual(listing.lifecycle_status, VehicleListing.LIFECYCLE_WITHDRAWN)
        self.assertIsNotNone(listing.delisted_at)
        self.assertIsNone(listing.days_to_sell)

    def test_reactivate_listing_clears_delisted_fields(self):
        listing = _make_listing(self.user)
        listing.first_listed_at = timezone.now() - timezone.timedelta(days=3)
        listing.save()
        mark_listing_sold(listing)

        reactivate_listing(listing)
        listing.refresh_from_db()

        self.assertEqual(listing.status, "completed")
        self.assertEqual(listing.lifecycle_status, VehicleListing.LIFECYCLE_ACTIVE)
        self.assertIsNone(listing.delisted_at)
        self.assertIsNone(listing.days_to_sell)

    def test_timestamps_are_stored_in_utc(self):
        listing = _make_listing(self.user)
        mark_listing_sold(listing)
        listing.refresh_from_db()
        self.assertTrue(timezone.is_aware(listing.delisted_at))
        self.assertEqual(listing.delisted_at.utcoffset().total_seconds(), 0)


class VehicleListingListedOnEndpointTests(TestCase):
    """Integration tests for the /listed-on/ endpoint — the real trigger for
    first_listed_at and relist_count in production (called by the extension
    on every publish/relist)."""

    def setUp(self):
        self.user = User.objects.create_user(email="dealer2@example.com", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse("update_vehicle_listing_listed_on")

    def _patch(self, listing_id, listed_on_iso):
        return self.client.patch(
            self.url,
            data=json.dumps({"id": listing_id, "listed_on": listed_on_iso}),
            content_type="application/json",
        )

    def test_first_publish_sets_first_listed_at(self):
        listing = _make_listing(self.user, status="pending")
        first_publish_time = timezone.now().isoformat()

        response = self._patch(listing.id, first_publish_time)
        listing.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(listing.first_listed_at)
        self.assertEqual(listing.relist_count, 0)
        self.assertEqual(listing.lifecycle_status, VehicleListing.LIFECYCLE_ACTIVE)

    def test_relist_increments_count_without_overwriting_first_listed_at(self):
        listing = _make_listing(self.user, status="pending")
        first_publish_time = timezone.now()
        self._patch(listing.id, first_publish_time.isoformat())
        listing.refresh_from_db()
        original_first_listed_at = listing.first_listed_at

        later_time = first_publish_time + timezone.timedelta(days=7)
        self._patch(listing.id, later_time.isoformat())
        listing.refresh_from_db()

        self.assertEqual(listing.relist_count, 1)
        # The whole point of first_listed_at: unchanged by the relist, even
        # though listed_on itself moved forward.
        self.assertEqual(listing.first_listed_at, original_first_listed_at)
        self.assertNotEqual(listing.listed_on, original_first_listed_at)

    def test_relisting_a_sold_listing_reactivates_it(self):
        listing = _make_listing(self.user, status="pending")
        self._patch(listing.id, timezone.now().isoformat())
        listing.refresh_from_db()
        mark_listing_sold(listing)
        listing.refresh_from_db()
        self.assertEqual(listing.lifecycle_status, VehicleListing.LIFECYCLE_SOLD)

        self._patch(listing.id, timezone.now().isoformat())
        listing.refresh_from_db()

        self.assertEqual(listing.lifecycle_status, VehicleListing.LIFECYCLE_ACTIVE)
        self.assertIsNone(listing.delisted_at)
        self.assertIsNone(listing.days_to_sell)
        self.assertEqual(listing.relist_count, 1)


class WithdrawEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="dealer3@example.com", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_withdraw_endpoint_marks_listing_withdrawn(self):
        listing = _make_listing(self.user)
        response = self.client.patch(
            reverse("withdraw_vehicle_listing"),
            data=json.dumps({"id": listing.id}),
            content_type="application/json",
        )
        listing.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(listing.lifecycle_status, VehicleListing.LIFECYCLE_WITHDRAWN)
        self.assertIsNotNone(listing.delisted_at)

    def test_cannot_withdraw_another_users_listing(self):
        other_user = User.objects.create_user(email="other@example.com", password="pw")
        listing = _make_listing(other_user)

        response = self.client.patch(
            reverse("withdraw_vehicle_listing"),
            data=json.dumps({"id": listing.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
