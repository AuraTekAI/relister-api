"""search_products' stock_number range filter (stock_number_min/_max).

Mirrors the existing price_min/price_max, year_min/year_max pattern in
views.py: stock_number is a CharField (some sources' stock codes are
alphanumeric, e.g. "A1234"), so the range filter casts to int behind a
regex guard that excludes non-numeric values rather than erroring out. The
pre-existing exact-match `stock_number` param is untouched and still works
alongside the new range params.

Run with:
    python src/manage.py test VehicleListing.tests_search_products_stock_number --settings=relister.settings_test
"""
from django.test import TestCase

from accounts.models import User

from .models import Vehicle, VehicleListing

SEARCH_URL = "/api/vehicle-listing/search/"


class SearchProductsStockNumberRangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="dealer@test.invalid", password="x")

    def _listing(self, stock_number, **overrides):
        vehicle = Vehicle.objects.create(make="Toyota", model="Corolla", year="2020")
        defaults = dict(
            user=self.user, vehicle=vehicle, list_id=f"listing-{stock_number}",
            stock_number=stock_number, price="20000", is_listed=True, status="completed",
        )
        defaults.update(overrides)
        return VehicleListing.objects.create(**defaults)

    def test_range_includes_only_stock_numbers_within_bounds(self):
        self._listing("50")
        self._listing("150")
        self._listing("250")

        response = self.client.get(SEARCH_URL, {"stock_number_min": "100", "stock_number_max": "200"})

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)

    def test_min_only_is_inclusive_and_open_ended(self):
        self._listing("100")
        self._listing("200")
        self._listing("300")

        response = self.client.get(SEARCH_URL, {"stock_number_min": "200"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)  # 200 and 300, inclusive

    def test_max_only_is_inclusive_and_open_ended(self):
        self._listing("100")
        self._listing("200")
        self._listing("300")

        response = self.client.get(SEARCH_URL, {"stock_number_max": "200"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)  # 100 and 200, inclusive

    def test_non_numeric_stock_numbers_are_excluded_from_range_query_not_erroring(self):
        """An alphanumeric stock code (e.g. from a source that formats them
        that way) can't participate in a numeric range — it must be silently
        excluded, and the request must not 500."""
        self._listing("150")
        self._listing("A1234")

        response = self.client.get(SEARCH_URL, {"stock_number_min": "0", "stock_number_max": "999999"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)

    def test_no_range_params_returns_everything_unfiltered_by_stock_number(self):
        self._listing("1")
        self._listing("999")

        response = self.client.get(SEARCH_URL, {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)

    def test_exact_match_stock_number_param_still_works_alongside_range(self):
        """The pre-existing exact-match filter must be untouched by adding
        the range params."""
        self._listing("221")
        self._listing("222")

        response = self.client.get(SEARCH_URL, {"stock_number": "221"})

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)

    def test_invalid_range_value_returns_400_not_a_crash(self):
        response = self.client.get(SEARCH_URL, {"stock_number_min": "not-a-number"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("stock_number_min", response.json()["errors"])
