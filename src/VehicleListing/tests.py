import json
from itertools import count

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import Vehicle, VehicleListing
from .price_estimation import estimate_price, _parse_price, _mileage_matches

User = get_user_model()

# VehicleListing is unique per (user, list_id, seller_profile_id), so every
# fixture listing needs its own list_id.
_list_ids = count(1)


def _sold_listing(user, price, make="Toyota", model="Corolla", variant="Ascent Sport",
                  transmission="Automatic", year="2020", mileage=42000):
    vehicle = Vehicle.objects.create(
        make=make, model=model, variant=variant, transmission=transmission, year=year, mileage=mileage,
    )
    return VehicleListing.objects.create(
        user=user, vehicle=vehicle, price=price, status="sold",
        list_id=f"test-{next(_list_ids)}",
    )


class PriceEstimationUtilsTests(TestCase):
    def test_parse_price_handles_currency_formatting(self):
        self.assertEqual(_parse_price("$18,500"), 18500.0)
        self.assertEqual(_parse_price("18500"), 18500.0)

    def test_parse_price_rejects_unparsable_values(self):
        self.assertIsNone(_parse_price("POA"))
        self.assertIsNone(_parse_price(None))
        self.assertIsNone(_parse_price(""))
        self.assertIsNone(_parse_price("$0"))

    def test_mileage_window_is_500km_at_the_strict_tier(self):
        self.assertTrue(_mileage_matches(4400, 4000, 500))    # inside the window
        self.assertTrue(_mileage_matches(4500, 4000, 500))    # exactly on the edge
        self.assertFalse(_mileage_matches(4600, 4000, 500))   # just outside
        self.assertFalse(_mileage_matches(None, 4000, 500))   # unknown odometer never matches
        self.assertTrue(_mileage_matches(999_999, 4000, None))  # loosest tier ignores mileage


class PriceEstimationAlgorithmTests(TestCase):
    def setUp(self):
        self.dealer = User.objects.create_user(email="pricedealer@example.com", password="pw")

    def _criteria(self, **overrides):
        base = {
            "make": "Toyota", "model": "Corolla", "variant": "Ascent Sport",
            "transmission": "Automatic", "year": 2020, "mileage": 42000,
        }
        base.update(overrides)
        return base

    def test_exact_tier_used_when_enough_matches(self):
        for price in [15000, 16000, 17000, 18000, 19000]:
            _sold_listing(self.dealer, str(price))

        result = estimate_price(self._criteria())

        self.assertFalse(result["insufficient_data"])
        self.assertEqual(result["tier"], "exact")
        self.assertFalse(result["widened"])
        self.assertEqual(result["sample_size"], 5)
        self.assertEqual(result["estimated_price"], 17000.0)
        self.assertEqual(result["price_range"], {"low": 15500.0, "high": 18500.0})

    def test_widens_mileage_window_when_500km_is_too_tight(self):
        # Two sold within ±500 km of the target — not enough on its own.
        _sold_listing(self.dealer, "17000", mileage=42000)
        _sold_listing(self.dealer, "17500", mileage=42300)
        # Three more of the same car further out; only counted once the
        # mileage window widens to ±2,000 km.
        _sold_listing(self.dealer, "16000", mileage=43500)
        _sold_listing(self.dealer, "18000", mileage=43800)
        _sold_listing(self.dealer, "19000", mileage=40500)

        result = estimate_price(self._criteria(mileage=42000))

        self.assertFalse(result["insufficient_data"])
        self.assertEqual(result["tier"], "widen_mileage")
        self.assertTrue(result["widened"])
        self.assertEqual(result["sample_size"], 5)

    def test_widens_when_variant_does_not_match_enough_listings(self):
        # Only 2 sold with the exact variant — not enough on its own.
        _sold_listing(self.dealer, "17000", variant="Ascent Sport")
        _sold_listing(self.dealer, "17500", variant="Ascent Sport")
        # 3 more of the same car but a different (or unlabeled) variant —
        # only enough once variant is dropped from the match.
        _sold_listing(self.dealer, "16000", variant="SX")
        _sold_listing(self.dealer, "18000", variant="SX")
        _sold_listing(self.dealer, "19000", variant=None)

        result = estimate_price(self._criteria())

        self.assertFalse(result["insufficient_data"])
        self.assertEqual(result["tier"], "drop_variant")
        self.assertTrue(result["widened"])
        self.assertEqual(result["sample_size"], 5)

    def test_only_sold_listings_are_used(self):
        for price, status in [
            ("15000", "sold"),
            ("16000", "sold"),
            ("17000", "sold"),
            ("18000", "sold"),
            ("999999", "completed"),   # still listed — must be ignored
            ("1", "withdrawn"),        # withdrawn — must be ignored
            ("2", "pending"),          # pending — must be ignored
        ]:
            vehicle = Vehicle.objects.create(
                make="Toyota", model="Corolla", variant="Ascent Sport",
                transmission="Automatic", year="2020", mileage=42000,
            )
            VehicleListing.objects.create(
                user=self.dealer, vehicle=vehicle, price=price, status=status,
                list_id=f"test-{next(_list_ids)}",
            )

        # Only 4 genuinely sold — one short of the minimum, so even after
        # full widening this must report insufficient data, proving the
        # non-sold rows (which would otherwise push the count to 7) were
        # correctly excluded throughout every tier.
        result = estimate_price(self._criteria())
        self.assertTrue(result["insufficient_data"])
        self.assertEqual(result["sample_size"], 4)

    def test_insufficient_data_when_fewer_than_minimum_even_at_loosest_tier(self):
        _sold_listing(self.dealer, "15000")
        _sold_listing(self.dealer, "16000")

        result = estimate_price(self._criteria())

        self.assertTrue(result["insufficient_data"])
        self.assertIsNone(result["estimated_price"])
        self.assertIsNone(result["price_range"])
        self.assertEqual(result["sample_size"], 2)
        self.assertIn("2 matching sold vehicle", result["message"])

    def test_unparsable_prices_are_excluded_from_the_sample(self):
        for price in ["15000", "16000", "17000", "18000", "19000", "POA"]:
            _sold_listing(self.dealer, price)

        result = estimate_price(self._criteria())

        self.assertEqual(result["sample_size"], 5)

    def test_make_and_model_are_never_relaxed(self):
        for price in ["15000", "16000", "17000", "18000", "19000"]:
            _sold_listing(self.dealer, price, make="Honda", model="Civic")

        # Asking for a Toyota Corolla must never fall back to Honda Civic
        # sales, no matter how loose the other criteria get.
        result = estimate_price(self._criteria(make="Toyota", model="Corolla"))
        self.assertTrue(result["insufficient_data"])


class PriceEstimationEndpointTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="priceadmin@example.com", password="pw")
        self.admin.is_staff = True
        self.admin.save()
        self.dealer = User.objects.create_user(email="pricedealer2@example.com", password="pw")
        self.client = APIClient()
        self.url = reverse("estimate_vehicle_price")

        for price in [15000, 16000, 17000, 18000, 19000]:
            _sold_listing(self.dealer, str(price))

    def _post(self, payload, user=None):
        self.client.force_authenticate(user=user or self.admin)
        return self.client.post(self.url, data=json.dumps(payload), content_type="application/json")

    def test_returns_estimate_for_admin(self):
        response = self._post({
            "make": "Toyota", "model": "Corolla", "variant": "Ascent Sport",
            "transmission": "Automatic", "year": 2020, "mileage": 42000,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["insufficient_data"])
        self.assertEqual(body["estimated_price"], 17000.0)

    def test_rejects_non_admin(self):
        response = self._post(
            {"make": "Toyota", "model": "Corolla", "year": 2020, "mileage": 42000},
            user=self.dealer,
        )
        self.assertEqual(response.status_code, 403)

    def test_requires_make_and_model(self):
        response = self._post({"year": 2020, "mileage": 42000})
        self.assertEqual(response.status_code, 400)

    def test_requires_valid_year_and_mileage(self):
        response = self._post({"make": "Toyota", "model": "Corolla", "year": "not-a-year", "mileage": 42000})
        self.assertEqual(response.status_code, 400)

        response = self._post({"make": "Toyota", "model": "Corolla", "year": 2020, "mileage": -5})
        self.assertEqual(response.status_code, 400)
