"""Tests for the Gumtree re-list status fix.

A car marked sold (it vanished from the seller's Gumtree profile) and then
re-listed must have BOTH its `sales` flag AND its `status` restored. Before the
fix, the end-of-sync "still present on profile" pass only reset `sales=False`,
leaving status="sold" for same-ad-id re-lists — and the extension publish guard
and relist queue exclude on status="sold", so those cars never got re-published.

Covers both cases the user asked about:
  * SAME ad id  -> reactivate_still_present_listings (the still-present bulk pass)
  * CHANGED ad id -> reactivate_listing (the per-listing matched path building block)
"""
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from VehicleListing.models import VehicleListing
from VehicleListing.utils import reactivate_listing, reactivate_still_present_listings


class RelistStatusTests(TestCase):
    SELLER = "seller-123"

    def setUp(self):
        self.user = User.objects.create_user(email="dealer@example.com", password="x")

    def _make(self, list_id, status, sales, sold=False, seller=None):
        return VehicleListing.objects.create(
            user=self.user,
            list_id=list_id,
            seller_profile_id=seller if seller is not None else self.SELLER,
            status=status,
            sales=sales,
            sold_at=timezone.now() if sold else None,
        )

    # --- Case 1: same ad id, car marked sold then re-listed ------------------

    def test_same_ad_id_sold_row_is_reactivated(self):
        """A sold row still present on the profile is set back to "pending" (needs to be
        published to Facebook again) — the same status a brand-new scraped listing gets —
        not just sales reset."""
        row = self._make("AD1", status="sold", sales=True, sold=True)

        reactivated, sales_reset = reactivate_still_present_listings(
            self.user, self.SELLER, ["AD1"]
        )

        row.refresh_from_db()
        self.assertEqual(row.status, "pending")  # the actual fix: re-queued for publish
        self.assertFalse(row.sales)
        self.assertIsNone(row.sold_at)
        self.assertEqual(reactivated, 1)
        self.assertEqual(sales_reset, 1)

    def test_non_sold_still_present_row_only_gets_sales_reset(self):
        """Existing behaviour preserved: a live (non-sold) row only has sales cleared,
        its status is left untouched."""
        row = self._make("AD2", status="completed", sales=True)

        reactivated, sales_reset = reactivate_still_present_listings(
            self.user, self.SELLER, ["AD2"]
        )

        row.refresh_from_db()
        self.assertEqual(row.status, "completed")
        self.assertFalse(row.sales)
        self.assertEqual(reactivated, 0)
        self.assertEqual(sales_reset, 1)

    def test_rows_not_in_incoming_ids_are_untouched(self):
        """Listings absent from the incoming profile call are left exactly as-is."""
        present = self._make("AD3", status="sold", sales=True, sold=True)
        absent = self._make("AD4", status="sold", sales=True, sold=True)

        reactivate_still_present_listings(self.user, self.SELLER, ["AD3"])

        present.refresh_from_db()
        absent.refresh_from_db()
        self.assertEqual(present.status, "pending")
        self.assertEqual(absent.status, "sold")  # untouched
        self.assertTrue(absent.sales)

    def test_other_seller_rows_are_untouched(self):
        """Scoped to the seller profile being synced."""
        mine = self._make("AD5", status="sold", sales=True, sold=True)
        other = self._make("AD5", status="sold", sales=True, sold=True, seller="other-seller")

        reactivate_still_present_listings(self.user, self.SELLER, ["AD5"])

        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(mine.status, "pending")
        self.assertEqual(other.status, "sold")  # different profile, untouched

    def test_empty_incoming_ids_is_a_noop(self):
        """Guard: an empty incoming set never resets anything (mirrors the scraper,
        which skips the whole block when the API returned no listings)."""
        row = self._make("AD6", status="sold", sales=True, sold=True)

        reactivated, sales_reset = reactivate_still_present_listings(self.user, self.SELLER, [])

        row.refresh_from_db()
        self.assertEqual(row.status, "sold")
        self.assertEqual((reactivated, sales_reset), (0, 0))

    # --- Case 2: changed ad id (per-listing matched path) -------------------

    def test_changed_ad_id_reactivate_listing_restores_status(self):
        """The changed-ad-id path calls reactivate_listing(matched) directly; confirm
        that building block fully un-sells a row."""
        row = self._make("OLD-AD", status="sold", sales=True, sold=True)

        reactivate_listing(row)

        row.refresh_from_db()
        self.assertEqual(row.status, "completed")
        self.assertFalse(row.sales)
        self.assertIsNone(row.sold_at)
