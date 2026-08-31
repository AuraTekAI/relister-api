"""CarsForSale: a vehicle must store ONLY its own photos.

The bug
-------
`CarsForSaleAdapter._parse_images` scoped the gallery to `div.swiper.vehicle`.
Verified against the live hydrated DOM, those containers are NOT the vehicle's
gallery — they are the related-stock rails ("similar vehicles", "more from this
dealer").

Measured on the reference URL
(2019 Suzuki Swift GL Navigator, .../mVSN-4GYZ4ygOsnslJCYgQ) — the fixture below
is that exact page, captured live:

  * 187 unique photo URLs exist page-wide
  * the Swift's own gallery holds 4 photos
  * the old scope returned 10 photos, and ZERO of them were the Swift's

So every carsforsale listing stored some other vehicle's photos — the reported
"Alto shows Corolla/Nissan images" symptom.

Two things make the page dangerous to read with a document-wide `find()`:

  1. It is a Framework7 SPA. The DOM keeps the page navigated FROM
     (`div.page...page-previous` — the showroom, ~190 other cars) next to the
     one requested (`div.page.automatic.vehicle.page-current`).
  2. Inside the current page, `div.swiper.vehicle` rails carry other cars.

The vehicle's real gallery is `div.vehicle-hero-carousel` inside
`page-current`, one `div.swiper-zoom-container` per photo, full-size JPG in
`data-cache`, slide order in `data-imgno`, and the vehicle's own name in each
slide's `alt`.

These tests assert on the adapter output AND on the rows/photo slots actually
written to the database by the real import pipeline.

Run with:
    python src/manage.py test VehicleListing.tests_carsforsale_image_ownership --settings=relister.settings_test
"""
import gzip
import pathlib
import re
from unittest import mock

from bs4 import BeautifulSoup
from django.test import TestCase

from accounts.models import User

from .custom_domain_adapters.carsforsale import CarsForSaleAdapter
from .custom_domain_scraper import custom_domain_profile_listings_thread
from .models import CustomDomainProfileListing, VehicleListing, VehicleListingImage

FIXTURE = pathlib.Path(__file__).parent / "test_fixtures" / "carsforsale_swift.html.gz"

SHOWROOM_URL = "https://carsforsale.com.au/showroom/mad-man-motors/cZS0__o4ZhSR9oNG06YeoQ"
PROFILE_ID = "carsforsale.com.au/showroom/mad-man-motors"
SWIFT_URL = (
    "https://carsforsale.com.au/cars/details/"
    "2019-suzuki-swift-gl-navigator-al/mVSN-4GYZ4ygOsnslJCYgQ"
)
SWIFT_LIST_ID = "mVSN-4GYZ4ygOsnslJCYgQ"
SWIFT_PHOTO_COUNT = 4
SWIFT_ALT = "2019 SUZUKI SWIFT Automatic Unleaded 5D HATCHBACK"
# The identity the photos must be filed under. Read from the page's own
# `page-current` container; the showroom left in the DOM behind it advertises a
# "2016 NISSAN SERENA ... $24,990" first, which is what used to win.
SWIFT_MAKE = "Suzuki"
SWIFT_MODEL = "SWIFT"
SWIFT_YEAR = "2019"
SWIFT_PRICE = 13990
FOREIGN_MAKE = "Nissan"
FOREIGN_PRICE = 24990

_PHOTO_RE = re.compile(
    r"https?://[^\"'\s]*virtualyard\.com\.au/photos/[A-Za-z0-9_\-]+\.jpg", re.I
)


def swift_html():
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as fh:
        return fh.read()


def _classes(value):
    return value if isinstance(value, list) else str(value).split()


def swift_own_photos(html):
    """Ground truth for "the Swift's photos", derived from the fixture
    independently of the adapter so the assertions can't be satisfied by the
    same mistake twice: the photo URLs inside the CURRENT vehicle page's
    hero carousel, keyed to the alt text each slide carries."""
    soup = BeautifulSoup(html, "html.parser")
    page = soup.find("div", class_=lambda c: bool(c)
                     and "page-current" in _classes(c) and "vehicle" in _classes(c))
    assert page is not None, "fixture has no page-current vehicle container"
    hero = page.find(class_=lambda c: bool(c) and "vehicle-hero-carousel" in _classes(c))
    assert hero is not None, "fixture has no vehicle-hero-carousel"

    urls, alts = set(), set()
    for slide in hero.find_all(class_="swiper-zoom-container"):
        img = slide.find("img")
        if img is None:
            continue
        alts.add((img.get("alt") or "").strip())
        for attr in ("data-cache", "data-desktopcache", "data-mobilecache"):
            val = (img.get(attr) or "").strip()
            if val and _PHOTO_RE.fullmatch(val):
                urls.add(val)
    return urls, alts


def foreign_photos(html):
    """Every photo URL on the page that is NOT the Swift's — the pool the old
    scope was drawing from."""
    own, _ = swift_own_photos(html)
    return set(_PHOTO_RE.findall(html)) - own


class FixtureSanityTests(TestCase):
    """Pin what the reference page actually contains, so a later fixture
    refresh that changes the shape fails loudly here."""

    def test_page_holds_many_other_vehicles_photos(self):
        html = swift_html()
        own, alts = swift_own_photos(html)
        page_wide = set(_PHOTO_RE.findall(html))

        self.assertEqual(alts, {SWIFT_ALT}, "gallery slides name more than one vehicle")
        self.assertGreater(
            len(page_wide), 100,
            "the page really does carry a large pool of other vehicles' photos",
        )
        self.assertGreater(len(foreign_photos(html)), 100)

    def test_the_swifts_gallery_has_four_photos(self):
        soup = BeautifulSoup(swift_html(), "html.parser")
        page = soup.find("div", class_=lambda c: bool(c)
                         and "page-current" in _classes(c) and "vehicle" in _classes(c))
        hero = page.find(class_=lambda c: bool(c) and "vehicle-hero-carousel" in _classes(c))
        self.assertEqual(
            len(hero.find_all(class_="swiper-zoom-container")), SWIFT_PHOTO_COUNT,
        )


class AdapterImageScopeTests(TestCase):
    def setUp(self):
        self.adapter = CarsForSaleAdapter(SHOWROOM_URL)
        self.html = swift_html()
        self.soup = BeautifulSoup(self.html, "html.parser")

    def test_returns_exactly_this_vehicles_four_photos(self):
        images = self.adapter._parse_images(self.soup)
        own, _ = swift_own_photos(self.html)

        self.assertEqual(
            len(images), SWIFT_PHOTO_COUNT,
            f"expected the Swift's {SWIFT_PHOTO_COUNT} photos, got {len(images)}",
        )
        self.assertTrue(set(images) <= own, "returned a photo that is not the Swift's")
        self.assertEqual(len(images), len(set(images)), "duplicate photo returned")

    def test_no_foreign_photo_is_returned(self):
        images = set(self.adapter._parse_images(self.soup))
        leaked = images & foreign_photos(self.html)
        self.assertFalse(leaked, f"{len(leaked)} photo(s) from other vehicles returned")

    def test_every_returned_photo_carries_this_vehicles_alt_text(self):
        """The page labels each gallery slide with its own vehicle. Using that
        as an independent ownership check."""
        alt_by_url = {}
        for slide in self.soup.find_all(class_="swiper-zoom-container"):
            img = slide.find("img")
            if img is None:
                continue
            for attr in ("data-cache", "data-desktopcache", "data-mobilecache"):
                val = (img.get(attr) or "").strip()
                if val:
                    alt_by_url[val] = (img.get("alt") or "").strip()

        images = self.adapter._parse_images(self.soup)
        self.assertEqual({alt_by_url.get(u) for u in images}, {SWIFT_ALT})

    def test_related_vehicle_rails_are_never_the_gallery(self):
        """`div.swiper.vehicle` was the old scope. Show those containers hold
        other cars, and that none of their photos come back."""
        rails = self.soup.find_all(
            "div", class_=lambda c: bool(c) and "swiper" in c and "vehicle" in c
        )
        self.assertGreater(len(rails), 0, "fixture should still contain the rails")
        rail_photos = set()
        for rail in rails:
            rail_photos.update(_PHOTO_RE.findall(str(rail)))
        self.assertGreater(len(rail_photos), 0)

        own, _ = swift_own_photos(self.html)
        self.assertFalse(rail_photos & own, "rails and the gallery should be disjoint")
        self.assertFalse(set(self.adapter._parse_images(self.soup)) & rail_photos)

    def test_previous_page_in_the_dom_is_not_read(self):
        """The SPA keeps the showroom we navigated from in the DOM. Its cars'
        photos must not be reachable."""
        prev = self.soup.find("div", class_=lambda c: bool(c) and "page-previous" in _classes(c))
        self.assertIsNotNone(prev, "fixture should still contain page-previous")
        prev_photos = set(_PHOTO_RE.findall(str(prev)))
        self.assertFalse(set(self.adapter._parse_images(self.soup)) & prev_photos)

    def test_missing_gallery_returns_nothing_rather_than_foreign_photos(self):
        """A template change must degrade to zero photos — the listing is then
        skipped by the extension's two-image minimum, which is the safe
        outcome. Publishing another vehicle's photos is not."""
        soup = BeautifulSoup(self.html, "html.parser")
        for node in soup.find_all(class_="swiper-zoom-container"):
            node.decompose()
        for node in soup.find_all(class_=lambda c: bool(c)
                                  and "vehicle-hero-carousel" in _classes(c)):
            node.decompose()

        with self.assertLogs("custom_domain", level="ERROR"):
            self.assertEqual(self.adapter._parse_images(soup), [])

    def test_parse_listing_result_carries_only_this_vehicles_photos(self):
        own, _ = swift_own_photos(self.html)
        with mock.patch(
            "VehicleListing.custom_domain_adapters.carsforsale._render",
            return_value=self.html,
        ):
            result = self.adapter.parse_listing(SWIFT_URL)

        self.assertIsNotNone(result)
        self.assertEqual(result["list_id"], SWIFT_LIST_ID)
        self.assertEqual(len(result["image"]), SWIFT_PHOTO_COUNT)
        self.assertTrue(set(result["image"]) <= own)


class DatabaseImageOwnershipTests(TestCase):
    """Run the real import pipeline and inspect what is actually stored."""

    def setUp(self):
        self.user = User.objects.create_user(email="cfs@test.invalid", password="x")
        self.profile = CustomDomainProfileListing.objects.create(
            url=SHOWROOM_URL, user=self.user, status="pending",
            profile_id=PROFILE_ID, domain=PROFILE_ID, total_listings=0,
        )
        self.html = swift_html()
        self.own, _ = swift_own_photos(self.html)
        self.foreign = foreign_photos(self.html)

        for target in (
            "VehicleListing.custom_domain_scraper.time.sleep",
        ):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

        render = mock.patch(
            "VehicleListing.custom_domain_adapters.carsforsale._render",
            return_value=self.html,
        )
        render.start()
        self.addCleanup(render.stop)

        self.adapter = CarsForSaleAdapter(SHOWROOM_URL)

    def scrape(self):
        custom_domain_profile_listings_thread(
            [SWIFT_URL], self.profile, self.user, PROFILE_ID, self.adapter,
        )

    def row(self):
        return VehicleListing.objects.get(
            user=self.user, seller_profile_id=PROFILE_ID, list_id=SWIFT_LIST_ID
        )

    def test_stored_images_column_holds_only_the_swifts_photos(self):
        self.scrape()
        stored = list(self.row().images or [])

        self.assertEqual(len(stored), SWIFT_PHOTO_COUNT, f"stored {len(stored)} photos")
        self.assertTrue(set(stored) <= self.own, "stored a photo that is not the Swift's")
        self.assertFalse(set(stored) & self.foreign, "stored another vehicle's photo")

    def test_image_table_rows_belong_only_to_this_listing(self):
        """The photo slot table (VehicleListingImage) is what the publish path
        reads, so check it directly rather than trusting the column."""
        self.scrape()
        row = self.row()

        slots = list(
            row.image_slots.order_by("position").values_list("source_url", flat=True)
        )
        self.assertEqual(len(slots), SWIFT_PHOTO_COUNT)
        self.assertEqual(slots, list(row.images or []), "slots diverge from the column")
        self.assertTrue(set(slots) <= self.own)
        self.assertFalse(set(slots) & self.foreign)

        # No slot anywhere in the table points at a foreign photo, and every
        # slot in the table belongs to this one listing.
        every = VehicleListingImage.objects.all()
        self.assertEqual(every.count(), SWIFT_PHOTO_COUNT)
        self.assertEqual({s.listing_id for s in every}, {row.pk})
        self.assertFalse(
            {s.source_url for s in every} & self.foreign,
            "a foreign photo reached the image table",
        )

    def test_positions_are_the_gallery_order_and_unique(self):
        self.scrape()
        positions = list(
            self.row().image_slots.order_by("position").values_list("position", flat=True)
        )
        self.assertEqual(positions, list(range(SWIFT_PHOTO_COUNT)))

    def test_re_scraping_does_not_add_or_swap_photos(self):
        self.scrape()
        before = list(self.row().images or [])
        before_slots = set(
            self.row().image_slots.values_list("source_url", flat=True)
        )

        self.scrape()
        after = list(self.row().images or [])
        after_slots = set(self.row().image_slots.values_list("source_url", flat=True))

        self.assertEqual(before, after, "a re-scrape changed the stored photos")
        self.assertEqual(before_slots, after_slots)
        self.assertEqual(
            VehicleListingImage.objects.count(), SWIFT_PHOTO_COUNT,
            "re-scraping accumulated extra photo rows",
        )


class PhotosAreFiledUnderTheRightVehicleTests(TestCase):
    """Storing the right photos is only half of "this product's own images" —
    they also have to hang off a row that IS this product.

    `_parse_title` / `_parse_price` used document-wide `find()` calls, so on
    this page they returned the FIRST `.cardTitle` / `.details-price` in the
    markup — both inside `page-previous`, the showroom's 169 other cars. The
    Swift's photos were therefore stored on a row named "2016 Nissan SERENA"
    priced at the Serena's $24,990, which in the database is indistinguishable
    from "this product has another vehicle's images".
    """

    def setUp(self):
        self.adapter = CarsForSaleAdapter(SHOWROOM_URL)
        self.html = swift_html()

    def _result(self):
        with mock.patch(
            "VehicleListing.custom_domain_adapters.carsforsale._render",
            return_value=self.html,
        ):
            return self.adapter.parse_listing(SWIFT_URL)

    def test_identity_comes_from_the_current_vehicle_page(self):
        result = self._result()
        self.assertEqual(result["make"], SWIFT_MAKE)
        self.assertEqual(result["model"], SWIFT_MODEL)
        self.assertEqual(result["year"], SWIFT_YEAR)
        self.assertNotEqual(
            result["make"], FOREIGN_MAKE,
            "identity was read from the showroom page left in the DOM",
        )

    def test_price_comes_from_the_current_vehicle_page(self):
        result = self._result()
        self.assertEqual(result["price"], SWIFT_PRICE)
        self.assertNotEqual(result["price"], FOREIGN_PRICE)

    def test_specs_belong_to_this_vehicle(self):
        result = self._result()
        self.assertEqual(result["vin"], "JSAAZC83S00309643")  # JSA… = Suzuki
        self.assertEqual(result["mileage"], 99100)
        self.assertEqual(result["color"], "Blue")
        self.assertEqual(result["fuel_type"], "Unleaded")
        self.assertIn("HATCHBACK", result["body_type"])

    def test_stored_row_identity_and_photos_agree(self):
        """The end state in the database: a Suzuki Swift row holding the Swift's
        four photos."""
        user = User.objects.create_user(email="cfs2@test.invalid", password="x")
        profile = CustomDomainProfileListing.objects.create(
            url=SHOWROOM_URL, user=user, status="pending",
            profile_id=PROFILE_ID, domain=PROFILE_ID, total_listings=0,
        )
        own, _ = swift_own_photos(self.html)
        with mock.patch("VehicleListing.custom_domain_scraper.time.sleep"), \
             mock.patch(
                 "VehicleListing.custom_domain_adapters.carsforsale._render",
                 return_value=self.html,
             ):
            custom_domain_profile_listings_thread(
                [SWIFT_URL], profile, user, PROFILE_ID, self.adapter,
            )

        row = VehicleListing.objects.get(user=user, list_id=SWIFT_LIST_ID)
        self.assertEqual(row.make, SWIFT_MAKE)
        self.assertEqual(row.model, SWIFT_MODEL)
        self.assertEqual(row.year, SWIFT_YEAR)
        self.assertEqual(row.price, str(SWIFT_PRICE))
        self.assertEqual(len(row.images or []), SWIFT_PHOTO_COUNT)
        self.assertTrue(set(row.images) <= own)
