"""Cross-vehicle image contamination regression tests.

Reported symptom: an Alto's listing had Corolla / Nissan photos stored against
it. Root cause was in image EXTRACTION, not storage — `_sync_listing_images`
reconciles whatever list the adapter returns, so a contaminated list is
persisted verbatim.

Three extraction paths could borrow another vehicle's photos:

  1. generic_jsonld._extract_carssr_images  — regexed the WHOLE decoded RSC
     payload, which also carries the dealer's related/similar/recently-viewed
     stock. This is the widest blast radius: the generic adapter is the
     fallback for every dealer domain without a hand-written adapter.
  2. buckinghamautos._extract_images        — same whole-payload regex.
  3. carsforsale._parse_images              — hero-scoped, but fell back to the
     whole page, which includes the `seller-all` block holding the dealer's
     entire inventory.

Each test below asserts the contaminating photos are absent AND the correct
ones are still present, so a fix can't pass by simply returning nothing.

Run with:
    python src/manage.py test VehicleListing.tests_image_scoping --settings=relister.settings_test
"""
import json

from django.test import SimpleTestCase

from .custom_domain_adapters.base import carssr_image_urls
from .custom_domain_adapters.buckinghamautos import _extract_images as buckingham_images
from .custom_domain_adapters.carsforsale import CarsForSaleAdapter
from .custom_domain_adapters.generic_jsonld import _extract_carssr_images


# The vehicle actually being scraped (an Alto) and two unrelated cars whose
# data sits in the same RSC payload as "related stock" — exactly the shape
# that produced the reported bug.
ALTO_PHOTOS = [
    "https://cdn.example.com/photo/alto-1.jpg",
    "https://cdn.example.com/photo/alto-2.jpg",
    "https://cdn.example.com/photo/alto-3.jpg",
]
FOREIGN_PHOTOS = [
    "https://cdn.example.com/photo/corolla-1.jpg",
    "https://cdn.example.com/photo/nissan-1.jpg",
]

ALTO_CAR = {
    "id": 4242,
    "name": "2013 Suzuki Alto GL",
    "make": "Suzuki",
    "model": "Alto",
    "year": 2013,
    "images": [
        {"position": i, "image": {"url": url}} for i, url in enumerate(ALTO_PHOTOS)
    ],
}


def _rsc_payload_with_related_stock():
    """A decoded RSC payload: this vehicle's carSSR object followed by the
    related-vehicles rail. Both use the same `"image":{"url":...}` encoding,
    which is why the old whole-payload regex could not tell them apart."""
    related = [
        {"name": "2014 Toyota Corolla", "image": {"url": FOREIGN_PHOTOS[0]}},
        {"name": "2016 Nissan X-Trail", "image": {"url": FOREIGN_PHOTOS[1]}},
    ]
    return (
        '{"carSSR":'
        + json.dumps(ALTO_CAR)
        + ',"relatedVehicles":'
        + json.dumps(related)
        + "}"
    )


class GenericCarssrImageScopingTests(SimpleTestCase):
    """generic_jsonld is the catch-all adapter for every unregistered dealer
    domain, so contamination here affected the most dealers."""

    def test_only_this_vehicles_photos_are_returned(self):
        rsc = _rsc_payload_with_related_stock()
        images = _extract_carssr_images(ALTO_CAR, rsc)

        self.assertEqual(images, ALTO_PHOTOS, "photo set is not exactly this vehicle's")
        for foreign in FOREIGN_PHOTOS:
            self.assertNotIn(foreign, images, "another vehicle's photo leaked in")

    def test_thumbnails_are_still_dropped(self):
        car = {
            "images": [
                {"image": {"url": "https://cdn.example.com/photo/alto-1.jpg"}},
                {"image": {"url": "https://cdn.example.com/photo/alto-1-thumb.jpg"}},
            ]
        }
        images = _extract_carssr_images(car, json.dumps({"carSSR": car}))
        self.assertEqual(images, ["https://cdn.example.com/photo/alto-1.jpg"])

    def test_repeated_url_inside_the_car_object_is_deduped(self):
        car = {
            "hero": {"image": {"url": ALTO_PHOTOS[0]}},
            "gallery": [{"image": {"url": ALTO_PHOTOS[0]}}, {"image": {"url": ALTO_PHOTOS[1]}}],
        }
        self.assertEqual(
            _extract_carssr_images(car, ""), [ALTO_PHOTOS[0], ALTO_PHOTOS[1]]
        )


class BuckinghamCarssrImageScopingTests(SimpleTestCase):
    def test_only_this_vehicles_photos_are_returned(self):
        rsc = _rsc_payload_with_related_stock()
        images = buckingham_images(ALTO_CAR, rsc)

        self.assertEqual(images, ALTO_PHOTOS)
        for foreign in FOREIGN_PHOTOS:
            self.assertNotIn(foreign, images)

    def test_photo_path_filter_is_preserved(self):
        """Buckingham keeps only /photo/ URLs — non-gallery assets (logos,
        banners) living in the car object must still be excluded."""
        car = {
            "images": [
                {"image": {"url": "https://cdn.example.com/photo/real-1.jpg"}},
                {"image": {"url": "https://cdn.example.com/assets/logo.jpg"}},
                {"image": {"url": "https://cdn.example.com/photo/real-thumb.jpg"}},
            ]
        }
        self.assertEqual(
            buckingham_images(car, ""), ["https://cdn.example.com/photo/real-1.jpg"]
        )


class CarssrFallbackTests(SimpleTestCase):
    """The scoped walk must degrade to the legacy scan rather than to nothing:
    zero photos would drop the listing under the extension's two-image minimum
    and block publishing entirely."""

    def test_falls_back_to_payload_scan_when_car_object_has_no_photos(self):
        rsc = '{"gallery":[{"image":{"url":"https://cdn.example.com/photo/a.jpg"}}]}'
        images = carssr_image_urls({"make": "Suzuki", "model": "Alto"}, rsc)
        self.assertEqual(images, ["https://cdn.example.com/photo/a.jpg"])

    def test_scoped_photos_win_over_the_payload_scan(self):
        """When the car object does carry photos, the payload is never consulted
        — otherwise the fallback would re-introduce the contamination."""
        rsc = _rsc_payload_with_related_stock()
        self.assertEqual(carssr_image_urls(ALTO_CAR, rsc), ALTO_PHOTOS)

    def test_no_photos_anywhere_returns_empty(self):
        self.assertEqual(carssr_image_urls({"make": "Suzuki"}, "{}"), [])

    def test_non_dict_car_object_does_not_raise(self):
        self.assertEqual(carssr_image_urls(None, "{}"), [])


# carsforsale.com.au detail page with the hero carousel MISSING (the template
# change that triggered the whole-page fallback), plus the dealer's full-stock
# block that the fallback used to harvest.
CARSFORSALE_NO_HERO = """
<html><body>
  <h1 class="cardTitle">2013 SUZUKI ALTO<br><small>GL</small></h1>
  <span class="details-price">$7,990</span>
  <div class="gallery-fallback">
    <img data-cache="https://virtualyard.com.au/photos/alto_a.jpg">
    <img data-cache="https://virtualyard.com.au/photos/alto_b.jpg">
  </div>
  <div class="stacked carousel seller-all">
    <img data-cache="https://virtualyard.com.au/photos/corolla_x.jpg">
    <img data-cache="https://virtualyard.com.au/photos/nissan_y.jpg">
  </div>
</body></html>
"""

CARSFORSALE_WITH_HERO = """
<html><body>
  <h1 class="cardTitle">2013 SUZUKI ALTO<br><small>GL</small></h1>
  <span class="details-price">$7,990</span>
  <div class="swiper vehicle">
    <img data-cache="https://virtualyard.com.au/photos/alto_a.jpg">
    <img data-cache="https://virtualyard.com.au/photos/alto_b.jpg">
  </div>
  <div class="stacked carousel seller-all">
    <img data-cache="https://virtualyard.com.au/photos/corolla_x.jpg">
  </div>
</body></html>
"""


class CarsForSaleImageScopingTests(SimpleTestCase):
    def setUp(self):
        self.adapter = CarsForSaleAdapter(
            "https://carsforsale.com.au/showroom/mad-man-motors/cZS0__o4ZhSR9oNG06YeoQ"
        )

    def _images(self, html):
        from bs4 import BeautifulSoup

        return self.adapter._parse_images(BeautifulSoup(html, "html.parser"))

    def test_hero_scoping_excludes_dealer_stock(self):
        images = self._images(CARSFORSALE_WITH_HERO)
        self.assertEqual(
            images,
            [
                "https://virtualyard.com.au/photos/alto_a.jpg",
                "https://virtualyard.com.au/photos/alto_b.jpg",
            ],
        )

    def test_fallback_without_hero_still_excludes_dealer_stock(self):
        """The regression: with no hero carousel the old code scanned the whole
        page and picked up `seller-all`, attaching a Corolla and a Nissan photo
        to this Alto."""
        images = self._images(CARSFORSALE_NO_HERO)

        self.assertIn("https://virtualyard.com.au/photos/alto_a.jpg", images)
        self.assertIn("https://virtualyard.com.au/photos/alto_b.jpg", images)
        self.assertNotIn("https://virtualyard.com.au/photos/corolla_x.jpg", images)
        self.assertNotIn("https://virtualyard.com.au/photos/nissan_y.jpg", images)

    def test_fallback_does_not_mutate_the_caller_s_soup(self):
        """The fallback decomposes foreign-stock nodes, so it must work on a
        copy — other parsers (title, price, specs) read the same soup after."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(CARSFORSALE_NO_HERO, "html.parser")
        self.adapter._parse_images(soup)
        self.assertIsNotNone(
            soup.find(class_="seller-all"),
            "foreign-stock block was removed from the caller's tree",
        )
        self.assertIsNotNone(soup.find(class_="cardTitle"))
