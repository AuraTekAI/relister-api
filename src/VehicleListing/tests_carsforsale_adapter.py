"""carsforsale.com.au (VirtualYard marketplace) adapter tests.

The DOM fixtures below mirror the real hydrated structure confirmed against the
live site: a `.cardTitle` h1 (YEAR MAKE MODEL + <small>VARIANT</small>), a
`.details-price`, VirtualYard `item-title`/`item-after` spec rows, a
`swiper vehicle` hero gallery whose photos carry the full-size JPG in
`data-cache`, and a separate `stacked carousel seller-all` block of OTHER
vehicles that must NOT leak into this listing's images.

_render is monkeypatched so these run offline (no ZenRows/network).

Run with:
    python src/manage.py test VehicleListing.tests_carsforsale_adapter --settings=relister.settings_test
"""
from unittest import mock

from django.test import SimpleTestCase

from .custom_domain_adapters import any_needs_image_proxy, resolve_for_url
from .custom_domain_adapters.carsforsale import CarsForSaleAdapter

SHOWROOM_URL = "https://carsforsale.com.au/showroom/mad-man-motors/cZS0__o4ZhSR9oNG06YeoQ"

SHOWROOM_HTML = """
<html><body>
  <div class="featured-info">
    <a href="/details/2015-nissan-serena-highway-star-g-hybrid-c26/AAA111"></a>
    <h3 class="cardTitle">2015 NISSAN SERENA</h3>
  </div>
  <div class="featured-info">
    <i class="btn-share-page" data-href="/cars/details/2016-land-rover-range-rover-evoque/BBB222"></i>
  </div>
  <!-- duplicate rendition of the same vehicle id must dedupe -->
  <a href="/cars/details/2015-nissan-serena-highway-star-g-hybrid-c26/AAA111"></a>
  <span>Holland Park West, QLD</span>
</body></html>
"""

DETAIL_HTML = """
<html><head>
  <meta name="description" content="FOR SALE: 2015 Nissan Serena Hybrid, 8-seater family van.">
  <meta property="og:image" content="https://virtualyard.com.au/photos/HERO0.jpg">
</head><body>
  <!-- The SPA keeps the page we navigated FROM in the DOM. Its cards use the
       same cardTitle/details-price classes and come FIRST in the markup, so a
       document-wide find() reads THIS car instead of the one requested. -->
  <div class="page automatic home with-hero page-previous">
    <h3 class="cardTitle">2022 VOLKSWAGEN AMAROK<br><small>W580X</small></h3>
    <span class="details-price">$61,990</span>
    <img data-cache="https://virtualyard.com.au/photos/PREVPAGECAR.jpg">
  </div>

  <div class="page automatic vehicle page-current">
  <h1 class="line-clamp cardTitle">2015 NISSAN SERENA<br><small>HIGHWAY STAR G (HYBRID) C26</small></h1>
  <div class="vehicle-details-price"><span class="details-price">$18,990</span></div>

  <!-- The REAL gallery for this vehicle: `vehicle-hero-carousel`, one
       `swiper-zoom-container` per photo, full-size JPG in data-cache, order in
       data-imgno. Confirmed against the live hydrated page. -->
  <div class="vehicle-hero-carousel open-fullscreen">
    <div class="swiper-slide">
      <div class="swiper-zoom-container">
        <img alt="2015 NISSAN SERENA HIGHWAY STAR G"
             src="https://storage.googleapis.com/au-assets/thumb0.webp"
             data-cache="https://virtualyard.com.au/photos/PHOTO0.jpg"
             data-mobilecache="https://virtualyard.com.au/photos/PHOTO0_SMALL.jpg"
             data-imgno="1">
      </div>
    </div>
    <div class="swiper-slide">
      <div class="swiper-zoom-container">
        <img alt="2015 NISSAN SERENA HIGHWAY STAR G"
             src="https://storage.googleapis.com/au-assets/thumb1.webp"
             data-cache="https://virtualyard.com.au/photos/PHOTO1.jpg"
             data-imgno="2">
      </div>
    </div>
    <!-- duplicate photo must dedupe -->
    <div class="swiper-slide">
      <div class="swiper-zoom-container">
        <img alt="2015 NISSAN SERENA HIGHWAY STAR G"
             data-cache="https://virtualyard.com.au/photos/PHOTO0.jpg"
             data-imgno="3">
      </div>
    </div>
  </div>

  <!-- `swiper vehicle` is NOT the gallery — it is the related-stock rail.
       Verified live: on a real detail page these hold entirely different cars. -->
  <div class="swiper vehicle swiper-initialized">
    <div class="swiper-slide slidex vehicle">
      <img data-cache="https://virtualyard.com.au/photos/RAILCAR1.jpg">
    </div>
  </div>

  <ul class="spec">
    <li><div class="item-inner"><div class="item-title">Badge</div><div class="item-after">HIGHWAY STAR G</div></div></li>
    <li><div class="item-inner"><div class="item-title">Transmission</div><div class="item-after">Automatic</div></div></li>
    <li><div class="item-inner"><div class="item-title">Colour</div><div class="item-after">PURPLE</div></div></li>
    <li><div class="item-inner"><div class="item-title">Body</div><div class="item-after">5D WAGON</div></div></li>
    <li><div class="item-inner"><div class="item-title">Odometer</div><div class="item-after">113,152 km</div></div></li>
    <li><div class="item-inner"><div class="item-title">Fuel Type</div><div class="item-after">Hybrid</div></div></li>
    <li><div class="item-inner"><div class="item-title">VIN</div><div class="item-after">6ZZF000HC26129397</div></div></li>
    <li><div class="item-inner"><div class="item-title">Seats</div><div class="item-after">8</div></div></li>
  </ul>

  <!-- The dealer's OTHER stock — its photos must NOT be picked up. -->
  <div class="stacked carousel seller-all">
    <img data-cache="https://virtualyard.com.au/photos/OTHERCAR9.jpg">
  </div>
  </div><!-- /page-current -->
</body></html>
"""

# Multi-word make, to prove resolve_make peels "Land Rover" off correctly.
LANDROVER_DETAIL = """
<html><head><meta name="description" content="Evoque"></head><body>
  <div class="page automatic vehicle page-current">
  <h1 class="cardTitle">2016 LAND ROVER RANGE ROVER EVOQUE<br><small>SD4 PURE</small></h1>
  <span class="details-price">$29,990</span>
  <div class="vehicle-hero-carousel">
    <div class="swiper-zoom-container">
      <img alt="2016 LAND ROVER RANGE ROVER EVOQUE"
           data-cache="https://virtualyard.com.au/photos/L1.jpg" data-imgno="1">
    </div>
    <div class="swiper-zoom-container">
      <img alt="2016 LAND ROVER RANGE ROVER EVOQUE"
           data-cache="https://virtualyard.com.au/photos/L2.jpg" data-imgno="2">
    </div>
  </div>
  <div class="item-title">Odometer</div><div class="item-after">90,000 km</div>
  </div>
</body></html>
"""


class ResolveTests(SimpleTestCase):
    def test_marketplace_url_resolves_to_dealer_scoped_adapter(self):
        adapter = resolve_for_url(SHOWROOM_URL)
        self.assertIsInstance(adapter, CarsForSaleAdapter)
        # seller_profile_id is scoped to the dealer, not the bare host.
        self.assertEqual(adapter.HOST, "carsforsale.com.au/showroom/mad-man-motors")

    def test_two_dealers_get_distinct_identities(self):
        a = resolve_for_url("https://carsforsale.com.au/showroom/mad-man-motors/x1")
        b = resolve_for_url("https://carsforsale.com.au/showroom/topcar-rez/x2")
        self.assertNotEqual(a.HOST, b.HOST)

    def test_www_host_also_resolves(self):
        adapter = resolve_for_url("https://www.carsforsale.com.au/showroom/foo/x1")
        self.assertIsInstance(adapter, CarsForSaleAdapter)


class DiscoveryTests(SimpleTestCase):
    def setUp(self):
        self.adapter = CarsForSaleAdapter(SHOWROOM_URL)

    def test_discovers_unique_detail_urls(self):
        with mock.patch.object(
            __import__("VehicleListing.custom_domain_adapters.carsforsale", fromlist=["_render"]),
            "_render", return_value=SHOWROOM_HTML,
        ):
            links = self.adapter.discover_stock_links(SHOWROOM_URL)
        # AAA111 (twice) + BBB222 → 2 unique, canonicalised to /cars/details/.
        self.assertEqual(len(links), 2)
        self.assertTrue(all(l.startswith("https://carsforsale.com.au/cars/details/") for l in links))
        self.assertIn("AAA111", links[0])

    def test_extract_listing_id(self):
        self.assertEqual(
            self.adapter.extract_listing_id(
                "https://carsforsale.com.au/cars/details/2015-nissan-serena/AAA111"),
            "AAA111",
        )


def _patch_render(html):
    mod = __import__("VehicleListing.custom_domain_adapters.carsforsale", fromlist=["_render"])
    return mock.patch.object(mod, "_render", return_value=html)


class ParseTests(SimpleTestCase):
    def setUp(self):
        self.adapter = CarsForSaleAdapter(SHOWROOM_URL)

    def test_parses_every_mapped_field(self):
        with _patch_render(DETAIL_HTML):
            r = self.adapter.parse_listing(
                "https://carsforsale.com.au/cars/details/2015-nissan-serena/AAA111")
        self.assertEqual(r["year"], "2015")
        self.assertEqual(r["make"], "Nissan")
        self.assertEqual(r["model"], "SERENA")
        self.assertEqual(r["variant"], "HIGHWAY STAR G (HYBRID) C26")
        self.assertEqual(r["transmission"], "Automatic")
        self.assertEqual(r["fuel_type"], "Hybrid")
        self.assertEqual(r["color"], "Purple")            # PURPLE → title-cased
        self.assertEqual(r["body_type"], "WAGON")         # "5D WAGON" → door prefix stripped
        self.assertEqual(r["vin"], "6ZZF000HC26129397")
        self.assertEqual(r["mileage"], 113152)            # "113,152 km" → int
        self.assertFalse(r["mileage_unavailable"])
        self.assertEqual(r["price"], 18990)               # "$18,990" → int
        self.assertEqual(r["list_id"], "AAA111")

    def test_images_are_scoped_to_this_vehicle_and_deduped(self):
        with _patch_render(DETAIL_HTML):
            r = self.adapter.parse_listing(
                "https://carsforsale.com.au/cars/details/2015-nissan-serena/AAA111")
        # PHOTO0 + PHOTO1 (PHOTO0's duplicate slide collapsed), in data-imgno
        # order. One canonical URL per slide, so PHOTO0_SMALL — another
        # rendition of the same photo — is not a second entry.
        self.assertEqual(r["image"], [
            "https://virtualyard.com.au/photos/PHOTO0.jpg",
            "https://virtualyard.com.au/photos/PHOTO1.jpg",
        ])
        # Nothing from the related-stock rail, the dealer's other stock, or the
        # previous page the SPA left in the DOM.
        for marker in ("RAILCAR", "OTHERCAR", "PREVPAGECAR", "PHOTO0_SMALL"):
            self.assertTrue(
                all(marker not in u for u in r["image"]),
                f"{marker} leaked into this vehicle's images",
            )

    def test_identity_is_read_from_the_current_page_not_the_previous_one(self):
        """The SPA leaves the previous page in the DOM and its cards come first
        in the markup, so a document-wide find() picks the wrong vehicle."""
        with _patch_render(DETAIL_HTML):
            r = self.adapter.parse_listing(
                "https://carsforsale.com.au/cars/details/2015-nissan-serena/AAA111")
        self.assertEqual(r["make"], "Nissan")
        self.assertEqual(r["price"], 18990)
        self.assertNotEqual(r["make"], "Volkswagen")
        self.assertNotEqual(r["price"], 61990)

    def test_description_from_meta(self):
        with _patch_render(DETAIL_HTML):
            r = self.adapter.parse_listing(
                "https://carsforsale.com.au/cars/details/2015-nissan-serena/AAA111")
        self.assertIn("Nissan Serena", r["description"])

    def test_multiword_make_split(self):
        with _patch_render(LANDROVER_DETAIL):
            r = self.adapter.parse_listing(
                "https://carsforsale.com.au/cars/details/2016-land-rover/CCC333")
        self.assertEqual(r["make"], "Land Rover")
        self.assertTrue(r["model"].upper().startswith("RANGE ROVER"))
        self.assertEqual(r["mileage"], 90000)

    def test_hollow_listing_is_skipped(self):
        with _patch_render("<html><body><div class='item-after'>x</div></body></html>"):
            r = self.adapter.parse_listing(
                "https://carsforsale.com.au/cars/details/x/DDD444")
        self.assertIsNone(r)

    def test_render_failure_returns_none(self):
        with _patch_render(None):
            r = self.adapter.parse_listing(
                "https://carsforsale.com.au/cars/details/x/EEE555")
        self.assertIsNone(r)

    def test_result_dict_keys_match_pipeline_contract(self):
        # The orchestrator + spec_from_result read exactly these keys; a missing
        # one would silently drop data into the void.
        with _patch_render(DETAIL_HTML):
            r = self.adapter.parse_listing(
                "https://carsforsale.com.au/cars/details/2015-nissan-serena/AAA111")
        for key in ("list_id", "title", "price", "description", "image", "location",
                    "year", "make", "model", "variant", "body_type", "fuel_type",
                    "color", "transmission", "vin", "mileage", "mileage_unavailable", "url"):
            self.assertIn(key, r)


class CarsForSaleImageProxyDecisionTests(SimpleTestCase):
    """carsforsale photos must reach the extension straight from
    virtualyard.com.au, never through custom_domain_image_proxy.

    virtualyard.com.au answers with `Access-Control-Allow-Origin: *`, so the
    browser can load it directly, and CarsForSaleAdapter says exactly that via
    KNOWN_HOSTS + needs_image_proxy() -> False. But the adapter is deliberately
    kept out of _REGISTRY (per-URL, see resolve_for_url) and
    any_needs_image_proxy() consulted _REGISTRY only, so its verdict was the
    unknown-host default: proxy. Specs rendered on the extension home page and
    photos did not, because the proxy hop was the only step that could fail.
    """

    def test_virtualyard_photo_host_is_not_proxied(self):
        self.assertFalse(any_needs_image_proxy(
            "https://virtualyard.com.au/photos/p39EYiIvzFSX0sA7kedXcxpYOa4.jpg"
        ))

    def test_carsforsale_own_hosts_are_not_proxied(self):
        for host in ("carsforsale.com.au", "www.carsforsale.com.au"):
            with self.subTest(host=host):
                self.assertFalse(any_needs_image_proxy(f"https://{host}/img/a.jpg"))

    def test_adapter_declaration_and_resolver_agree(self):
        # The regression was these two disagreeing: the adapter said "no proxy",
        # the resolver said "proxy".
        url = "https://virtualyard.com.au/photos/x.jpg"
        adapter = CarsForSaleAdapter(
            "https://carsforsale.com.au/showroom/mad-man-motors/cZS0")
        self.assertFalse(adapter.needs_image_proxy(url))
        self.assertEqual(adapter.needs_image_proxy(url), any_needs_image_proxy(url))

    def test_unknown_host_still_proxied(self):
        # The default must be unchanged for genuinely unknown CDNs.
        self.assertTrue(any_needs_image_proxy("https://some-random-cdn.example/a.jpg"))
