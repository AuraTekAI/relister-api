"""custom_domain_profile_listings_thread's multi-round retry.

Discovery finding N links is not the same as N rows landing in the database —
each link still needs its own parse_listing() call to succeed, and any one of
those can fail on a transient hiccup unrelated to the others. These tests
prove the retry-round logic recovers from that: a listing that fails a bounded
number of times within one run still ends up saved, spaced 20s apart (real
recovery time for a rate limit or proxy rotation, unlike a same-second retry),
without ever double-creating a row or waiting when nothing failed.

Run with:
    python src/manage.py test VehicleListing.tests_custom_domain_retry --settings=relister.settings_test
"""
from unittest import mock

from django.test import TestCase

from accounts.models import User

from .custom_domain_scraper import _FETCH_RETRY_DELAY_SECONDS, _MAX_FETCH_ROUNDS, custom_domain_profile_listings_thread
from .models import CustomDomainProfileListing, VehicleListing

PROFILE_ID = "dealer.example.com"


class _FlakyAdapter:
    """A fake adapter whose parse_listing() fails a configurable number of
    times per listing before succeeding — or never succeeds, for the
    "persistently broken" case. extract_listing_id is a plain URL suffix, no
    network involved."""

    def __init__(self, fail_counts=None):
        self.fail_counts = fail_counts or {}
        self.call_counts = {}

    def extract_listing_id(self, url):
        return url.rsplit("/", 1)[-1]

    def parse_listing(self, url):
        listing_id = self.extract_listing_id(url)
        self.call_counts[listing_id] = self.call_counts.get(listing_id, 0) + 1
        required_failures = self.fail_counts.get(listing_id, 0)
        if self.call_counts[listing_id] <= required_failures:
            return None
        idx = listing_id
        return {
            "list_id": listing_id, "title": f"Car {idx}", "price": 10000,
            "description": "desc", "image": [], "location": None,
            "year": "2020", "make": "Toyota", "model": f"Model-{idx}", "variant": None,
            "body_type": "Hatchback", "fuel_type": "Petrol", "color": "White",
            "transmission": "Automatic", "vin": None, "mileage": 1000,
            "mileage_unavailable": False, "url": url,
        }


def _urls(n, prefix="car"):
    return [f"https://dealer.example.com/buy/{prefix}-{i}" for i in range(n)]


class CustomDomainRetryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="dealer@test.invalid", password="x")
        self.profile = CustomDomainProfileListing.objects.create(
            user=self.user, url="https://dealer.example.com", profile_id=PROFILE_ID,
        )
        sleep_patcher = mock.patch("VehicleListing.custom_domain_scraper.time.sleep")
        self.mock_sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)
        # The small per-item politeness delay in _process_stock_url's "new
        # listing" path also goes through time.sleep — zero it out so it
        # can't be confused with the 20s round-retry delay in assertions.
        uniform_patcher = mock.patch("VehicleListing.custom_domain_scraper.random.uniform", return_value=0)
        uniform_patcher.start()
        self.addCleanup(uniform_patcher.stop)

    def _round_delay_calls(self):
        return [c for c in self.mock_sleep.call_args_list if c.args == (_FETCH_RETRY_DELAY_SECONDS,)]

    def _run(self, n, fail_counts=None):
        urls = _urls(n)
        adapter = _FlakyAdapter(fail_counts)
        custom_domain_profile_listings_thread(urls, self.profile, self.user, PROFILE_ID, adapter)
        return adapter

    def test_no_failures_saves_everything_without_any_retry_wait(self):
        self._run(5)
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 5)
        self.assertEqual(self._round_delay_calls(), [])

    def test_single_transient_failure_still_gets_saved(self):
        adapter = self._run(5, fail_counts={"car-2": 1})
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 5)
        self.assertTrue(VehicleListing.objects.filter(user=self.user, list_id="car-2").exists())
        self.assertEqual(adapter.call_counts["car-2"], 2, "car-2 should have been retried exactly once")
        self.assertEqual(len(self._round_delay_calls()), 1)

    def test_no_duplicate_row_created_for_a_retried_listing(self):
        self._run(3, fail_counts={"car-1": 2})
        self.assertEqual(
            VehicleListing.objects.filter(user=self.user, list_id="car-1").count(), 1,
            "a listing that failed then succeeded must not end up with two rows",
        )

    def test_failures_of_different_severity_all_recover_in_one_run(self):
        # car-1 fails once, car-5 fails twice, car-8 fails three times (right
        # at the edge of the round budget) — all in the SAME run.
        adapter = self._run(10, fail_counts={"car-1": 1, "car-5": 2, "car-8": 3})
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 10)
        for listing_id in ("car-1", "car-5", "car-8"):
            self.assertTrue(VehicleListing.objects.filter(user=self.user, list_id=listing_id).exists())
        # car-8 needed the full budget: 3 failures -> succeeds on the 4th
        # attempt, i.e. round 4 -> exactly 3 waits between rounds 1-4.
        self.assertEqual(len(self._round_delay_calls()), _MAX_FETCH_ROUNDS - 1)

    def test_persistent_failure_beyond_the_round_budget_is_left_out_not_crashed(self):
        adapter = self._run(4, fail_counts={"car-3": 99})  # never succeeds
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 3)
        self.assertFalse(VehicleListing.objects.filter(user=self.user, list_id="car-3").exists())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.processed_listings, 3)
        self.assertEqual(self.profile.status, "completed")
        # Exhausted every round trying — capped, not infinite.
        self.assertEqual(adapter.call_counts["car-3"], _MAX_FETCH_ROUNDS)
        self.assertEqual(len(self._round_delay_calls()), _MAX_FETCH_ROUNDS - 1)

    def test_large_batch_with_scattered_failures_all_saved(self):
        """20 items, a handful scattered through the batch failing 0/1/2
        times each — every single one must still land in the DB."""
        fail_counts = {f"car-{i}": (i % 3) for i in range(0, 20, 4)}  # car-0:0, car-4:1, car-8:2, car-12:0, car-16:1
        adapter = self._run(20, fail_counts=fail_counts)
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 20)
        self.assertEqual(
            sorted(VehicleListing.objects.filter(user=self.user).values_list("list_id", flat=True)),
            sorted(f"car-{i}" for i in range(20)),
        )

    def test_exception_during_processing_is_treated_as_a_retriable_failure(self):
        """An unexpected exception (bad image URL, adapter bug, ...) must be
        caught and retried like any other failure, not crash the thread."""
        urls = _urls(3)
        adapter = _FlakyAdapter()
        real_parse = adapter.parse_listing
        calls = {"n": 0}

        def flaky_parse(url):
            calls["n"] += 1
            if url.endswith("car-1") and calls["n"] < 4:
                raise ConnectionError("simulated transient network error")
            return real_parse(url)

        adapter.parse_listing = flaky_parse
        custom_domain_profile_listings_thread(urls, self.profile, self.user, PROFILE_ID, adapter)
        self.assertEqual(VehicleListing.objects.filter(user=self.user).count(), 3)

    def test_processed_listings_counts_only_actual_successes(self):
        self._run(6, fail_counts={"car-3": 99})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.processed_listings, 5)
