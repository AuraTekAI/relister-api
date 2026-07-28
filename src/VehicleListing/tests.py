import io
import json
from datetime import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from .models import Vehicle, VehicleListing
from .utils import mark_listing_sold, reactivate_listing, withdraw_listing
from .vehicle_matching import get_or_create_vehicle
from .export_utils import quarter_date_range, days_to_sell_percentiles


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


class VehicleMatchingTests(TestCase):
    """Confirms Vehicle dedup-by-VIN and the identity-conflict handling
    described in vehicle_matching.py."""

    def test_no_vin_always_creates_a_new_vehicle(self):
        result = {"make": "Toyota", "model": "Corolla", "year": "2020"}
        v1 = get_or_create_vehicle(result)
        v2 = get_or_create_vehicle(result)
        self.assertNotEqual(v1.id, v2.id)

    def test_same_vin_same_details_reuses_the_row(self):
        result = {"vin": "1HGCM82633A123456", "make": "Toyota", "model": "Corolla", "year": "2020", "color": "Red"}
        v1 = get_or_create_vehicle(result)
        v2 = get_or_create_vehicle(result)
        self.assertEqual(v1.id, v2.id)
        self.assertEqual(Vehicle.objects.filter(vin=result["vin"]).count(), 1)

    def test_same_vin_filling_in_previously_blank_fields_reuses_the_row(self):
        # First sight of this VIN has no color yet; a later scrape adds one —
        # filling a blank isn't a conflict.
        first = {"vin": "1HGCM82633A999999", "make": "Toyota", "model": "Corolla", "year": "2020"}
        v1 = get_or_create_vehicle(first)
        second = {**first, "color": "Blue"}
        v2 = get_or_create_vehicle(second)
        self.assertEqual(v1.id, v2.id)
        v1.refresh_from_db()
        self.assertEqual(v1.color, "Blue")

    def test_same_vin_conflicting_make_creates_a_new_unvinned_vehicle(self):
        # Real cars can't change make/model — a VIN match with a genuinely
        # different make is treated as a data anomaly, not the same car.
        first = {"vin": "1HGCM82633A555555", "make": "Toyota", "model": "Corolla", "year": "2020"}
        v1 = get_or_create_vehicle(first)

        conflicting = {"vin": "1HGCM82633A555555", "make": "Honda", "model": "Civic", "year": "2020"}
        v2 = get_or_create_vehicle(conflicting)

        self.assertNotEqual(v1.id, v2.id)
        self.assertIsNone(v2.vin)  # can't share the VIN — column is unique
        self.assertEqual(v2.make, "Honda")
        # The original VIN-holding row is untouched, not corrupted.
        v1.refresh_from_db()
        self.assertEqual(v1.make, "Toyota")

    def test_same_vin_conflicting_color_only_still_creates_a_new_vehicle(self):
        # Proves the check isn't just "make" — ANY single identity column
        # disagreeing is enough, even with make/model/year all matching.
        first = {
            "vin": "1HGCM82633A777777", "make": "Toyota", "model": "Corolla",
            "year": "2020", "color": "White", "body_type": "Sedan",
            "fuel_type": "Petrol", "transmission": "Automatic",
        }
        v1 = get_or_create_vehicle(first)

        color_conflict = {**first, "color": "Black"}
        v2 = get_or_create_vehicle(color_conflict)

        self.assertNotEqual(v1.id, v2.id)
        v1.refresh_from_db()
        self.assertEqual(v1.color, "White")  # untouched
        self.assertEqual(v2.color, "Black")

    def test_same_vin_differing_mileage_only_still_reuses_the_row(self):
        # Mileage is the one deliberate exception — an odometer reading is
        # SUPPOSED to change every time the same real car is rescraped, so it
        # must never be treated as "a different car".
        first = {"vin": "1HGCM82633A888888", "make": "Toyota", "model": "Corolla", "year": "2020", "mileage": 40000}
        v1 = get_or_create_vehicle(first)

        later = {**first, "mileage": 45000}
        v2 = get_or_create_vehicle(later)

        self.assertEqual(v1.id, v2.id)
        v1.refresh_from_db()
        self.assertEqual(v1.mileage, 45000)


class VehicleExportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="admin@example.com", password="pw")
        self.admin.is_staff = True
        self.admin.save()
        self.dealer = User.objects.create_user(email="dealer@example.com", password="pw")
        self.dealer.dealership_name = "Best Cars Perth"
        self.dealer.save()

        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.url = reverse("export_vehicle_data")

    def _make_sold_listing(self, days_to_sell, created_at=None):
        vehicle = Vehicle.objects.create(make="Toyota", model="Corolla", year="2020")
        listing = VehicleListing.objects.create(
            user=self.dealer, vehicle=vehicle, price="15000", status="sold",
            lifecycle_status=VehicleListing.LIFECYCLE_SOLD, days_to_sell=days_to_sell,
        )
        if created_at:
            VehicleListing.objects.filter(pk=listing.pk).update(created_at=created_at)
        return listing

    def test_non_admin_is_forbidden(self):
        self.client.force_authenticate(user=self.dealer)
        response = self.client.get(self.url, {"export_format": "json"})
        self.assertEqual(response.status_code, 403)

    def test_json_export_includes_dealer_join_and_stats(self):
        self._make_sold_listing(10)
        self._make_sold_listing(20)
        self._make_sold_listing(30)

        response = self.client.get(self.url, {"export_format": "json"})
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["count"], 3)
        self.assertEqual(body["sold_count"], 3)
        self.assertEqual(body["stats"]["median_days_to_sell"], 20)

        row = body["results"][0]
        self.assertEqual(row["dealership_name"], "Best Cars Perth")
        self.assertEqual(row["dealer_email"], "dealer@example.com")
        self.assertEqual(row["make"], "Toyota")
        # The aggregate stat is repeated on every row.
        self.assertEqual(row["median_days_to_sell"], 20)

    def test_non_sold_listings_excluded_from_stats(self):
        self._make_sold_listing(10)
        active_vehicle = Vehicle.objects.create(make="Honda", model="Civic", year="2021")
        VehicleListing.objects.create(
            user=self.dealer, vehicle=active_vehicle, price="20000", status="completed",
            lifecycle_status=VehicleListing.LIFECYCLE_ACTIVE,
        )

        response = self.client.get(self.url, {"export_format": "json"})
        body = response.json()
        self.assertEqual(body["count"], 2)       # both rows exported
        self.assertEqual(body["sold_count"], 1)  # only the sold one counted for stats

    def test_custom_date_range_filters_rows(self):
        in_range = timezone.make_aware(datetime(2025, 8, 15))
        out_of_range = timezone.make_aware(datetime(2025, 1, 1))
        self._make_sold_listing(5, created_at=in_range)
        self._make_sold_listing(5, created_at=out_of_range)

        response = self.client.get(self.url, {
            "export_format": "json", "start_date": "2025-07-01", "end_date": "2025-09-30",
        })
        body = response.json()
        self.assertEqual(body["count"], 1)

    def test_quarter_preset_matches_custom_range(self):
        in_q1 = timezone.make_aware(datetime(2025, 8, 1))
        self._make_sold_listing(5, created_at=in_q1)

        response = self.client.get(self.url, {
            "export_format": "json", "quarter": "Q1", "year": "2025", "year_type": "financial",
        })
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["start_date"], "2025-07-01")
        self.assertEqual(body["end_date"], "2025-09-30")

    def test_xlsx_format_returns_a_real_spreadsheet(self):
        self._make_sold_listing(15)
        response = self.client.get(self.url, {"export_format": "xlsx"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment; filename=", response["Content-Disposition"])

        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(response.content))
        sheet = workbook.active
        self.assertEqual(sheet.cell(row=1, column=1).value, "Listing ID")
        self.assertEqual(sheet.max_row, 2)  # header + 1 data row

    def test_csv_format_returns_correct_headers(self):
        self._make_sold_listing(15)
        response = self.client.get(self.url, {"export_format": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        first_line = response.content.decode().splitlines()[0]
        self.assertIn("Listing ID", first_line)
        self.assertIn("Dealership Name", first_line)

    def test_bad_format_is_rejected(self):
        response = self.client.get(self.url, {"export_format": "pdf"})
        self.assertEqual(response.status_code, 400)


class ExportUtilsTests(TestCase):
    def test_australian_fy_q1_is_july_to_september(self):
        start, end = quarter_date_range("Q1", 2025, "financial")
        self.assertEqual(start.isoformat(), "2025-07-01")
        self.assertEqual(end.isoformat(), "2025-09-30")

    def test_australian_fy_q3_rolls_into_next_calendar_year(self):
        start, end = quarter_date_range("Q3", 2025, "financial")
        self.assertEqual(start.isoformat(), "2026-01-01")
        self.assertEqual(end.isoformat(), "2026-03-31")

    def test_calendar_year_q1_is_january_to_march(self):
        start, end = quarter_date_range("Q1", 2025, "calendar")
        self.assertEqual(start.isoformat(), "2025-01-01")
        self.assertEqual(end.isoformat(), "2025-03-31")

    def test_percentiles_empty_input_returns_none_not_zero(self):
        stats = days_to_sell_percentiles([])
        self.assertIsNone(stats["median_days_to_sell"])
