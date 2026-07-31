"""Regression tests for the two Easy Vehicles image defects.

  Issue 1 — PARTIAL_IMAGE_UPLOAD: the extension only managed to upload a subset
            of a listing's photos because every publish live-proxied N full-size
            dealer originals through custom_domain_image_proxy. Fix under test:
            the hosting pipeline's FB-safe JPEG variant served by
            serializers._resolve_extension_images.

  Issue 4 — duplicate photos on Facebook: the VirtualYard gallery renders each
            photo at several sizes, each with its own signed URL, so an
            exact-string dedup kept both. Fix under test:
            EasyVehiclesAustraliaAdapter._parse_images' structural gallery parse.

The gallery tests run against real, unmodified HTML captured from the three
listings in the bug report (gzipped under test_fixtures/), so they assert
against the markup production actually sees rather than a hand-written mock.

Run with:
    python src/manage.py test VehicleListing.tests_image_fixes --settings=relister.settings_test
"""
import gzip
import io
import os
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.client import RequestFactory
from PIL import Image

from accounts.models import User

from .custom_domain_adapters.easyvehiclesaustralia import EasyVehiclesAustraliaAdapter
from .image_pipeline import (
    build_upload_variant_bytes,
    build_variant_bytes,
    content_hash_for,
    download_image_bytes,
    get_or_create_ready_hosted_image,
    public_url_for,
    s3_key_for,
    _sync_listing_images,
    sync_listing_images,
)
from .models import (
    CustomDomainProfileListing,
    GumtreeProfileListing,
    HostedImage,
    VehicleListing,
    VehicleListingImage,
)
from .serializers import _resolve_extension_images
from .tasks import process_vehicle_listing_image_task

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'test_fixtures')

# (fixture, the listing it corresponds to in the bug report)
LIVE_FIXTURES = [
    ('easyvehicles_i30_a.html.gz', '2018 Hyundai i30 Active (listing 9688 / 9686)'),
    ('easyvehicles_i30_b.html.gz', '2018 Hyundai i30 Active (the other i30)'),
    ('easyvehicles_asx.html.gz', '2017 Mitsubishi ASX LS (listing 9695)'),
]


def load_fixture(name):
    with gzip.open(os.path.join(FIXTURE_DIR, name), 'rt', encoding='utf-8', errors='replace') as fh:
        return fh.read()


def make_jpeg(width, height, color=(120, 30, 30)):
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buffer, format='JPEG', quality=90)
    return buffer.getvalue()


def make_png_with_alpha(width, height):
    buffer = io.BytesIO()
    Image.new('RGBA', (width, height), (10, 200, 10, 0)).save(buffer, format='PNG')
    return buffer.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Issue 4 — one URL per real photo
# ─────────────────────────────────────────────────────────────────────────────
@override_settings(EASYVEHICLES_VERIFY_IMAGE_URLS=False)
class EasyVehiclesGalleryParseTests(SimpleTestCase):
    """Gallery-shape assertions. The per-photo availability check is switched
    off here so these never touch the network — it has its own class below."""

    def setUp(self):
        self.adapter = EasyVehiclesAustraliaAdapter()

    def test_live_pages_yield_exactly_one_url_per_slide(self):
        """Every gallery slide contributes exactly one URL, and no URL repeats.

        This is the duplicate-images assertion: the page carries ~2.5x more
        au-assets URLs than it has photos (fullscreen + thumbnail + sidebar
        cars), and the parser must return only the fullscreen one per slide.
        """
        from bs4 import BeautifulSoup

        for fixture, label in LIVE_FIXTURES:
            with self.subTest(listing=label):
                html = load_fixture(fixture)
                slides = [
                    li for li in BeautifulSoup(html, 'html.parser')
                    .find('ul', class_='vehicle-photo-carousel')
                    .find_all('li', recursive=False)
                    if 'clone' not in (li.get('class') or [])
                ]
                images = self.adapter._parse_images(html)

                self.assertEqual(len(images), len(slides))
                self.assertEqual(len(set(images)), len(images), 'duplicate URLs returned')
                # Real dealer galleries here are 15+ photos; anything less would
                # trip the extension's minimum-image guard.
                self.assertGreaterEqual(len(images), 15)

    def test_live_pages_return_only_fullscreen_urls_not_thumbnails(self):
        """The returned URL for each slide is the slide's own data-src (the
        fullscreen rendition), never its data-thumb sibling."""
        from bs4 import BeautifulSoup

        for fixture, label in LIVE_FIXTURES:
            with self.subTest(listing=label):
                html = load_fixture(fixture)
                soup = BeautifulSoup(html, 'html.parser')
                slides = soup.find('ul', class_='vehicle-photo-carousel').find_all('li', recursive=False)
                fullsize = {li.get('data-src') for li in slides}
                thumbs = {li.get('data-thumb') for li in slides} - fullsize

                images = self.adapter._parse_images(html)
                self.assertTrue(set(images).issubset(fullsize))
                self.assertFalse(set(images) & thumbs, 'thumbnail renditions leaked into the result')

    def test_old_wholepage_scan_would_have_duplicated(self):
        """Guard on the premise: prove the naive whole-page scan really does
        return ~2x the photos on these exact pages, so this test file keeps
        failing loudly if anyone reverts to it."""
        import re

        for fixture, label in LIVE_FIXTURES:
            with self.subTest(listing=label):
                html = load_fixture(fixture)
                naive = set(re.findall(
                    r"https://storage\.googleapis\.com/au-assets/[A-Za-z0-9_\-./]+\.(?:jpe?g|png|webp)",
                    html,
                ))
                self.assertGreater(len(naive), len(self.adapter._parse_images(html)) * 1.5)

    def test_clone_slides_and_size_variants_are_skipped(self):
        """lightSlider's loop clones and per-slide size variants collapse to one
        URL per photo (synthetic markup — clones are injected client-side)."""
        html = """
        <ul class="vehicle-photo-carousel light-slider">
          <li data-thumb="https://storage.googleapis.com/au-assets/aaa_thumb.jpg"
              data-src="https://storage.googleapis.com/au-assets/aaa_full.jpg">
            <img src="https://storage.googleapis.com/au-assets/aaa_carousel.jpg">
          </li>
          <li data-thumb="https://storage.googleapis.com/au-assets/bbb_thumb.jpg"
              data-src="https://storage.googleapis.com/au-assets/bbb_full.jpg">
            <img src="https://storage.googleapis.com/au-assets/bbb_carousel.jpg">
          </li>
          <li class="clone" data-src="https://storage.googleapis.com/au-assets/aaa_full.jpg">
            <img src="https://storage.googleapis.com/au-assets/aaa_carousel.jpg">
          </li>
        </ul>
        """
        self.assertEqual(
            EasyVehiclesAustraliaAdapter()._parse_images(html),
            [
                'https://storage.googleapis.com/au-assets/aaa_full.jpg',
                'https://storage.googleapis.com/au-assets/bbb_full.jpg',
            ],
        )

    def test_slide_without_data_src_falls_back_to_inner_img(self):
        html = """
        <ul class="vehicle-photo-carousel">
          <li><img data-src="https://storage.googleapis.com/au-assets/only_img.jpg"></li>
        </ul>
        """
        self.assertEqual(
            EasyVehiclesAustraliaAdapter()._parse_images(html),
            ['https://storage.googleapis.com/au-assets/only_img.jpg'],
        )

    def test_missing_carousel_falls_back_to_page_scan_without_sidebar(self):
        """If the template changes, degrade to the old scan rather than to zero
        images — and still keep the 'Recent vehicles' sidebar out."""
        html = """
        <div><img src="https://storage.googleapis.com/au-assets/mine_1.jpg"></div>
        <div><img src="https://storage.googleapis.com/au-assets/mine_2.jpg"></div>
        <h3>Recent vehicles</h3>
        <div><img src="https://storage.googleapis.com/au-assets/other_car.jpg"></div>
        """
        images = EasyVehiclesAustraliaAdapter()._parse_images(html)
        self.assertEqual(images, [
            'https://storage.googleapis.com/au-assets/mine_1.jpg',
            'https://storage.googleapis.com/au-assets/mine_2.jpg',
        ])


# ─────────────────────────────────────────────────────────────────────────────
# Issue 1 (root cause) — photos whose googleapis copy is a 404
# ─────────────────────────────────────────────────────────────────────────────
class DeadImageUrlFallbackTests(SimpleTestCase):
    """VirtualYard publishes each photo at up to four addresses and which ones
    serve varies per photo, per listing and over time. Measured 2026-07-29 on
    the Suzuki Swift, slide 3: li.data-src 404, li.data-src-error 200/364KB,
    img.src 200/49KB — the same photo, one address dead and three alive. Storing
    only the dead one is what aborts the publish.

    The size floor matters just as much: on the Kizashi, data-src-error resolves
    to a single ~8KB placeholder shared by every slide, so "answers 200" is not
    sufficient to accept a URL.

    _url_is_live is mocked throughout — the suite must not hit the network.
    """

    FULL = 'https://storage.googleapis.com/au-assets/full.jpg'
    MIRROR = 'https://virtualyard.com.au/photos/full.jpg?w=2048'
    SHOWN = 'https://storage.googleapis.com/au-assets/shown.jpg'
    THUMB_MIRROR = 'https://virtualyard.com.au/photos/shown.jpg?w=640&h=480'

    def _html(self, full=None, mirror=None, shown=None, thumb_mirror=None):
        full = self.FULL if full is None else full
        mirror = self.MIRROR if mirror is None else mirror
        shown = self.SHOWN if shown is None else shown
        thumb_mirror = self.THUMB_MIRROR if thumb_mirror is None else thumb_mirror
        attrs = f'data-src="{full}"'
        if mirror:
            attrs += f' data-src-error="{mirror}"'
        if thumb_mirror:
            attrs += f' data-thumb-error="{thumb_mirror}"'
        img = f'<img src="{shown}">' if shown else ''
        return f'''
        <ul class="vehicle-photo-carousel">
          <li {attrs}>{img}</li>
        </ul>
        '''

    def _parse(self, html, live):
        with mock.patch.object(EasyVehiclesAustraliaAdapter, '_url_is_live',
                               side_effect=lambda url: live.get(url, False)) as probe:
            return EasyVehiclesAustraliaAdapter()._parse_images(html), probe

    @override_settings(EASYVEHICLES_VERIFY_IMAGE_URLS=True)
    def test_prefers_the_full_size_mirror_over_the_smaller_rendition(self):
        """The Swift's slide 3 exactly: primary dead, full-size mirror alive.
        Must take the mirror (364KB) rather than dropping to 640x480."""
        images, _ = self._parse(
            self._html(), {self.FULL: False, self.MIRROR: True, self.SHOWN: True},
        )
        self.assertEqual(images, [self.MIRROR])

    @override_settings(EASYVEHICLES_VERIFY_IMAGE_URLS=True)
    def test_falls_through_to_the_displayed_rendition_when_full_size_is_gone(self):
        images, _ = self._parse(
            self._html(), {self.FULL: False, self.MIRROR: False, self.SHOWN: True},
        )
        self.assertEqual(images, [self.SHOWN])

    @override_settings(EASYVEHICLES_VERIFY_IMAGE_URLS=True)
    def test_falls_through_to_the_thumbnail_mirror_as_a_last_resort(self):
        images, _ = self._parse(
            self._html(),
            {self.FULL: False, self.MIRROR: False, self.SHOWN: False, self.THUMB_MIRROR: True},
        )
        self.assertEqual(images, [self.THUMB_MIRROR])

    @override_settings(EASYVEHICLES_VERIFY_IMAGE_URLS=True)
    def test_live_full_size_is_kept_and_nothing_else_is_probed(self):
        images, probe = self._parse(self._html(), {self.FULL: True})
        self.assertEqual(images, [self.FULL], 'downgraded a photo that was serving fine')
        self.assertEqual(probe.call_count, 1)

    @override_settings(EASYVEHICLES_VERIFY_IMAGE_URLS=True)
    def test_all_renditions_dead_keeps_the_original(self):
        images, _ = self._parse(self._html(), {})
        self.assertEqual(images, [self.FULL])

    @override_settings(EASYVEHICLES_VERIFY_IMAGE_URLS=False)
    def test_kill_switch_skips_verification_entirely(self):
        images, probe = self._parse(self._html(), {})
        self.assertEqual(images, [self.FULL])
        self.assertEqual(probe.call_count, 0)

    @override_settings(EASYVEHICLES_VERIFY_IMAGE_URLS=True)
    def test_mixed_gallery_recovers_every_photo(self):
        """Listing 9701's shape: 5 of 23 full-size alive, the rest recoverable
        from another rendition. All 23 must come back, distinct."""
        slides = ''.join(
            f'<li data-src="https://storage.googleapis.com/au-assets/full{i}.jpg"'
            f'    data-src-error="https://virtualyard.com.au/photos/full{i}.jpg?w=2048">'
            f'  <img src="https://storage.googleapis.com/au-assets/shown{i}.jpg"></li>'
            for i in range(23)
        )
        live = {}
        for i in range(23):
            live[f'https://storage.googleapis.com/au-assets/full{i}.jpg'] = i < 5
            live[f'https://virtualyard.com.au/photos/full{i}.jpg?w=2048'] = 5 <= i < 15
            live[f'https://storage.googleapis.com/au-assets/shown{i}.jpg'] = True

        images, _ = self._parse(f'<ul class="vehicle-photo-carousel">{slides}</ul>', live)

        self.assertEqual(len(images), 23)
        self.assertEqual(len(set(images)), 23, 'duplicate URLs returned')
        self.assertEqual(sum(1 for u in images if '/full' in u), 15, 'lost a full-resolution photo')
        self.assertEqual(sum(1 for u in images if 'shown' in u), 8)


class UrlIsLiveTests(SimpleTestCase):
    """The probe itself — this is what keeps the placeholder out."""

    URL = 'https://storage.googleapis.com/au-assets/photo.jpg'

    def _head(self, status, length=None):
        headers = {} if length is None else {'Content-Length': str(length)}
        return mock.patch(
            'VehicleListing.custom_domain_adapters.easyvehiclesaustralia.requests.head',
            return_value=mock.Mock(status_code=status, headers=headers),
        )

    def test_placeholder_sized_response_is_rejected(self):
        """~8KB is the shared 'no photo' image, not a vehicle photo."""
        with self._head(200, 8_192):
            self.assertFalse(EasyVehiclesAustraliaAdapter._url_is_live(self.URL))

    def test_real_photo_sizes_are_accepted(self):
        for length in (45_000, 364_000, 489_000):
            with self.subTest(bytes=length):
                with self._head(200, length):
                    self.assertTrue(EasyVehiclesAustraliaAdapter._url_is_live(self.URL))

    def test_missing_content_length_falls_back_to_measuring_the_body(self):
        """The mirror host answers HEAD 200 with no Content-Length AND serves
        the ~9KB placeholder. Trusting that 200 is what put 53 placeholder URLs
        into production, so the body has to be measured."""
        with self._head(200), mock.patch.object(
            EasyVehiclesAustraliaAdapter, '_body_is_big_enough', return_value=False,
        ) as sized:
            self.assertFalse(EasyVehiclesAustraliaAdapter._url_is_live(self.URL))
        sized.assert_called_once_with(self.URL)

        with self._head(200), mock.patch.object(
            EasyVehiclesAustraliaAdapter, '_body_is_big_enough', return_value=True,
        ):
            self.assertTrue(EasyVehiclesAustraliaAdapter._url_is_live(self.URL))

    def test_body_measurement_rejects_the_placeholder(self):
        """9,158 bytes — the real placeholder size observed in production."""
        response = mock.MagicMock()
        response.status_code = 200
        response.iter_content = lambda size: [b'x' * 9158]
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *a: False
        with mock.patch('VehicleListing.custom_domain_adapters.easyvehiclesaustralia.requests.get',
                        return_value=response):
            self.assertFalse(EasyVehiclesAustraliaAdapter._body_is_big_enough(self.URL))

    def test_body_measurement_accepts_a_real_photo_and_stops_early(self):
        """Must not download the whole 400KB file just to size-check it."""
        chunks = [b'x' * 8192] * 50
        response = mock.MagicMock()
        response.status_code = 200
        response.iter_content = lambda size: iter(chunks)
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *a: False
        with mock.patch('VehicleListing.custom_domain_adapters.easyvehiclesaustralia.requests.get',
                        return_value=response):
            self.assertTrue(EasyVehiclesAustraliaAdapter._body_is_big_enough(self.URL))

    def test_body_measurement_treats_errors_as_live(self):
        with mock.patch('VehicleListing.custom_domain_adapters.easyvehiclesaustralia.requests.get',
                        side_effect=Exception('reset')):
            self.assertTrue(EasyVehiclesAustraliaAdapter._body_is_big_enough(self.URL))

    def test_status_codes(self):
        for status, expected in ((200, True), (405, True), (501, True), (404, False), (403, False)):
            with self.subTest(status=status):
                with self._head(status, 400_000):
                    self.assertIs(EasyVehiclesAustraliaAdapter._url_is_live(self.URL), expected)

    def test_transport_error_treated_as_live(self):
        """A flaky request must not downgrade a healthy photo."""
        with mock.patch('VehicleListing.custom_domain_adapters.easyvehiclesaustralia.requests.head',
                        side_effect=Exception('connection reset')):
            self.assertTrue(EasyVehiclesAustraliaAdapter._url_is_live(self.URL))



class ImageProxyRetryTests(SimpleTestCase):
    """The proxy is what serves any photo the pipeline hasn't hosted yet, so a
    transient upstream failure there is a dropped photo at publish time."""

    def setUp(self):
        self.request = RequestFactory().get(
            '/api/vehicle-listing/custom-domain-image/',
            {'url': 'https://storage.googleapis.com/au-assets/photo.jpg'},
        )
        patcher = mock.patch('VehicleListing.views.time.sleep')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _response(self, status, body=b'\xff\xd8\xff'):
        return mock.Mock(
            status_code=status,
            headers={'Content-Type': 'image/jpeg'},
            iter_content=lambda chunk_size: [body],
            close=mock.Mock(),
        )

    def test_transient_404_is_retried_and_recovers(self):
        from VehicleListing.views import custom_domain_image_proxy

        with mock.patch('VehicleListing.views._http_requests.get',
                        side_effect=[self._response(404), self._response(200)]) as get:
            response = custom_domain_image_proxy(self.request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_count, 2, '404 was treated as final — the photo is dropped')

    def test_settled_failures_are_not_retried(self):
        from VehicleListing.views import custom_domain_image_proxy

        for status in (403, 410):
            with self.subTest(status=status):
                with mock.patch('VehicleListing.views._http_requests.get',
                                return_value=self._response(status)) as get:
                    response = custom_domain_image_proxy(self.request)
                self.assertEqual(response.status_code, 502)
                self.assertEqual(get.call_count, 1)

    def test_persistent_404_still_gives_up(self):
        from VehicleListing.views import custom_domain_image_proxy

        with mock.patch('VehicleListing.views._http_requests.get',
                        return_value=self._response(404)) as get:
            response = custom_domain_image_proxy(self.request)

        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(get.call_count, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Issue 4 (second half) — duplicates must not be reintroduced downstream
# ─────────────────────────────────────────────────────────────────────────────
class SyncListingImagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.listing = VehicleListing.objects.create(user=self.user, list_id='L1', seller_profile_id='P1')

    def _sync(self, urls):
        """Run the reconcile with the Celery fan-out captured, not brokered.

        The fan-out is deferred to transaction.on_commit, which never fires
        inside TestCase's wrapping atomic block — captureOnCommitCallbacks runs
        it explicitly so the enqueue assertions mean something.
        """
        with mock.patch('VehicleListing.tasks.process_vehicle_listing_image_task.apply_async') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                sync_listing_images(self.listing, urls)
        return delay

    def test_one_slot_per_url_and_one_task_per_new_slot(self):
        urls = [f'https://storage.googleapis.com/au-assets/p{i}.jpg' for i in range(20)]
        delay = self._sync(urls)

        slots = list(self.listing.image_slots.order_by('position'))
        self.assertEqual(len(slots), 20)
        self.assertEqual([s.source_url for s in slots], urls)
        self.assertEqual([s.position for s in slots], list(range(20)))
        self.assertEqual(delay.call_count, 20)

    def test_rescrape_with_identical_urls_is_a_noop(self):
        urls = [f'https://storage.googleapis.com/au-assets/p{i}.jpg' for i in range(5)]
        self._sync(urls)
        delay = self._sync(urls)

        self.assertEqual(self.listing.image_slots.count(), 5)
        self.assertEqual(delay.call_count, 0, 're-scrape re-enqueued unchanged photos')

    def test_dropped_photo_is_removed_and_new_one_enqueued(self):
        self._sync(['https://x.invalid/a.jpg', 'https://x.invalid/b.jpg'])
        delay = self._sync(['https://x.invalid/a.jpg', 'https://x.invalid/c.jpg'])

        self.assertEqual(
            sorted(self.listing.image_slots.values_list('source_url', flat=True)),
            ['https://x.invalid/a.jpg', 'https://x.invalid/c.jpg'],
        )
        self.assertEqual(delay.call_count, 1)

    def test_scraper_failure_never_escapes_into_the_scrape_loop(self):
        with mock.patch('VehicleListing.image_pipeline._sync_listing_images', side_effect=RuntimeError('boom')):
            sync_listing_images(self.listing, ['https://x.invalid/a.jpg'])  # must not raise


class SyncListingImagesAutocommitTests(TransactionTestCase):
    """Same reconcile, but under production's autocommit semantics.

    TestCase wraps each test in an atomic block, which masks what a mid-loop
    IntegrityError actually does to the rows already written — so this case has
    to run without that wrapper.
    """

    def test_repeated_url_in_one_scrape_does_not_cost_the_listing_its_photos(self):
        """A scrape that hands the same URL twice must still slot every photo.

        (listing, source_url) is unique_together, so a duplicate URL makes the
        second create raise IntegrityError. sync_listing_images swallows it —
        which used to leave the listing with only the slots created *before*
        the duplicate: fewer hosted photos than the listing has, i.e. the
        PARTIAL_IMAGE_UPLOAD symptom.
        """
        user = User.objects.create_user(email='dup@test.invalid', password='x')
        listing = VehicleListing.objects.create(user=user, list_id='DUP', seller_profile_id='P')
        urls = [
            'https://x.invalid/a.jpg',
            'https://x.invalid/b.jpg',
            'https://x.invalid/a.jpg',  # duplicate rendition of the first photo
            'https://x.invalid/c.jpg',
        ]

        with mock.patch('VehicleListing.tasks.process_vehicle_listing_image_task.apply_async') as delay:
            sync_listing_images(listing, urls)

        self.assertEqual(
            sorted(listing.image_slots.values_list('source_url', flat=True)),
            ['https://x.invalid/a.jpg', 'https://x.invalid/b.jpg', 'https://x.invalid/c.jpg'],
        )
        self.assertEqual([s.position for s in listing.image_slots.order_by('position')], [0, 1, 2])
        self.assertEqual(delay.call_count, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Issue 1 — what the extension is actually told to upload
# ─────────────────────────────────────────────────────────────────────────────
class ExtensionImagePayloadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dealer2@test.invalid', password='x')
        self.listing = VehicleListing.objects.create(user=self.user, list_id='L2', seller_profile_id='P2')
        self.request = RequestFactory().get('/api/vehicle-listing/custom-domain-listings/')

    def _add_slot(self, position, ready=True, with_upload=True):
        source = f'https://storage.googleapis.com/au-assets/photo{position}.jpg'
        hosted = None
        if ready:
            hosted = HostedImage.objects.create(
                content_hash=f'{position:064d}',
                large_image=f'vehicle-images/aa/{position}/large.webp',
                upload_image=f'vehicle-images/aa/{position}/upload.jpg' if with_upload else '',
                status=HostedImage.STATUS_READY,
            )
        return VehicleListingImage.objects.create(
            listing=self.listing,
            source_url=source,
            position=position,
            hosted_image=hosted,
            status=VehicleListingImage.STATUS_READY if ready else VehicleListingImage.STATUS_PENDING,
        )

    @override_settings(EXTENSION_USE_HOSTED_IMAGES=True)
    def test_ready_photos_are_served_as_hosted_jpegs_not_proxied(self):
        for i in range(18):
            self._add_slot(i)

        urls = _resolve_extension_images(self.listing, self.request)

        self.assertEqual(len(urls), 18)
        self.assertEqual(len(set(urls)), 18, 'duplicate URLs in the extension payload')
        for url in urls:
            self.assertTrue(url.endswith('.jpg'), f'not a FB-accepted format: {url}')
            self.assertNotIn('custom-domain-image', url, 'still routed through the proxy')

    @override_settings(EXTENSION_USE_HOSTED_IMAGES=True)
    def test_unprocessed_photos_still_fall_back_to_the_proxy(self):
        self._add_slot(0)
        self._add_slot(1, ready=False)
        self._add_slot(2, with_upload=False)  # hosted but pre-backfill: no JPEG yet

        urls = _resolve_extension_images(self.listing, self.request)

        self.assertEqual(len(urls), 3, 'a photo went missing from the payload')
        self.assertTrue(urls[0].endswith('.jpg'))
        self.assertIn('custom-domain-image', urls[1])
        self.assertIn('custom-domain-image', urls[2])

    @override_settings(EXTENSION_USE_HOSTED_IMAGES=True)
    def test_ordering_follows_slot_position(self):
        for i in (2, 0, 1):
            self._add_slot(i)
        urls = _resolve_extension_images(self.listing, self.request)
        self.assertEqual(urls, sorted(urls, key=lambda u: int(u.split('/')[-2])))

    @override_settings(EXTENSION_USE_HOSTED_IMAGES=False)
    def test_kill_switch_restores_the_old_proxy_everything_behaviour(self):
        self.listing.images = ['https://storage.googleapis.com/au-assets/a.jpg']
        self.listing.save(update_fields=['images'])
        self._add_slot(0)

        urls = _resolve_extension_images(self.listing, self.request)
        self.assertEqual(len(urls), 1)
        self.assertIn('custom-domain-image', urls[0])

    @override_settings(EXTENSION_USE_HOSTED_IMAGES=True)
    def test_legacy_listing_without_slots_is_unaffected(self):
        self.listing.images = ['https://storage.googleapis.com/au-assets/a.jpg']
        self.listing.save(update_fields=['images'])

        urls = _resolve_extension_images(self.listing, self.request)
        self.assertEqual(len(urls), 1)
        self.assertIn('custom-domain-image', urls[0])


# ─────────────────────────────────────────────────────────────────────────────
# Gumtree image-CDN 403 — images.gumtree.com.au (Peakhour-fronted, Cloudinary-
# backed) blocks ZenRows' default datacenter-IP proxy tier with a hard 403,
# even though the exact same URL fetches fine from a real browser (which is
# how the Chrome extension gets it onto Facebook Marketplace). mode=auto asks
# ZenRows to escalate to residential proxies/JS rendering only when the cheap
# default path is blocked, fixing the download without paying premium-proxy
# cost on every other (non-Gumtree) image this function downloads.
# ─────────────────────────────────────────────────────────────────────────────
class DownloadImageBytesAntiBotModeTests(SimpleTestCase):
    @override_settings(ZENROWS_API_KEY='test-key')
    def test_gumtree_image_cdn_uses_premium_au_proxy_to_dodge_the_datacenter_ip_block(self):
        fake_response = mock.Mock()
        fake_response.headers = {'Content-Type': 'image/jpeg'}
        fake_response.content = b'\xff\xd8\xff'
        fake_response.raise_for_status = mock.Mock()

        with mock.patch('VehicleListing.image_pipeline.ZenRowsClient') as client_cls:
            client_cls.return_value.get.return_value = fake_response
            download_image_bytes('https://images.gumtree.com.au/image/private/t_$_20/move/x', timeout=35)

        client_cls.return_value.get.assert_called_once_with(
            'https://images.gumtree.com.au/image/private/t_$_20/move/x',
            params={'premium_proxy': 'true', 'proxy_country': 'au'},
            timeout=35,
        )

    @override_settings(ZENROWS_API_KEY='test-key')
    def test_non_gumtree_urls_keep_adaptive_stealth_mode_to_save_credits(self):
        fake_response = mock.Mock()
        fake_response.headers = {'Content-Type': 'image/jpeg'}
        fake_response.content = b'\xff\xd8\xff'
        fake_response.raise_for_status = mock.Mock()

        with mock.patch('VehicleListing.image_pipeline.ZenRowsClient') as client_cls:
            client_cls.return_value.get.return_value = fake_response
            download_image_bytes('https://dealer.example.com/photos/1.jpg', timeout=35)

        client_cls.return_value.get.assert_called_once_with(
            'https://dealer.example.com/photos/1.jpg',
            params={'mode': 'auto'},
            timeout=35,
        )

    @override_settings(ZENROWS_API_KEY='')
    def test_missing_api_key_fails_fast(self):
        with self.assertRaises(ValueError):
            download_image_bytes('https://images.gumtree.com.au/x.jpg', timeout=35)

    @override_settings(ZENROWS_API_KEY='test-key')
    def test_zr_content_type_wins_over_a_misleading_envelope_content_type(self):
        """Live-observed prod case: ZenRows' own response envelope said
        `text/plain` for a URL that was actually a real, successfully-fetched
        JPEG — it reports the ORIGIN resource's real type separately via
        `Zr-Content-Type`. Trusting the envelope header alone was rejecting
        good photos as 'unexpected content type' and permanently failing them
        on the first attempt (this is a ValueError, not a RequestException, so
        Celery's autoretry never even got a chance to run)."""
        fake_response = mock.Mock()
        fake_response.headers = {'Content-Type': 'text/plain; charset=utf-8', 'Zr-Content-Type': 'image/jpeg'}
        fake_response.content = b'\xff\xd8\xff\xdb\x00real-jpeg-bytes'
        fake_response.raise_for_status = mock.Mock()

        with mock.patch('VehicleListing.image_pipeline.ZenRowsClient') as client_cls:
            client_cls.return_value.get.return_value = fake_response
            data = download_image_bytes('https://images.gumtree.com.au/image/private/t_$_20/move/x', timeout=35)

        self.assertEqual(data, fake_response.content)

    @override_settings(ZENROWS_API_KEY='test-key')
    def test_falls_back_to_envelope_content_type_when_zr_header_absent(self):
        fake_response = mock.Mock()
        fake_response.headers = {'Content-Type': 'text/html'}
        fake_response.content = b'<html>not an image</html>'
        fake_response.raise_for_status = mock.Mock()

        with mock.patch('VehicleListing.image_pipeline.ZenRowsClient') as client_cls:
            client_cls.return_value.get.return_value = fake_response
            with self.assertRaises(ValueError):
                download_image_bytes('https://images.gumtree.com.au/image/private/t_$_20/move/x', timeout=35)


class UploadVariantTests(SimpleTestCase):
    def test_upload_variant_is_a_facebook_accepted_jpeg(self):
        data, width, height = build_upload_variant_bytes(make_jpeg(2400, 1600), 1600, 85)
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual(img.format, 'JPEG')
        self.assertEqual((width, height), (1600, 1067))

    def test_upload_variant_never_upscales(self):
        _data, width, height = build_upload_variant_bytes(make_jpeg(800, 600), 1600, 85)
        self.assertEqual((width, height), (800, 600))

    def test_transparent_source_is_flattened_not_rejected(self):
        data, _w, _h = build_upload_variant_bytes(make_png_with_alpha(400, 300), 1600, 85)
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual(img.format, 'JPEG')
            self.assertEqual(img.mode, 'RGB')

    def test_webp_variants_cover_all_three_storefront_sizes(self):
        variants = build_variant_bytes(make_jpeg(2400, 1600), {'thumbnail': 320, 'medium': 800, 'large': 1600}, 82)
        self.assertEqual(sorted(variants), ['large', 'medium', 'thumbnail'])
        for name, (data, width, _h) in variants.items():
            with Image.open(io.BytesIO(data)) as img:
                self.assertEqual(img.format, 'WEBP', name)
            self.assertLessEqual(width, {'thumbnail': 320, 'medium': 800, 'large': 1600}[name])


class HostedImageDedupTests(TestCase):
    """The pipeline's content-hash dedup, and its blind spot."""

    def setUp(self):
        patcher = mock.patch('VehicleListing.image_pipeline.upload_variant')
        self.upload = patcher.start()
        self.addCleanup(patcher.stop)

    def test_identical_bytes_are_uploaded_once_and_reused(self):
        data = make_jpeg(1200, 800)
        digest = content_hash_for(data)

        first, uploaded_first = get_or_create_ready_hosted_image(digest, 'https://x.invalid/a.jpg', data)
        calls_after_first = self.upload.call_count
        second, uploaded_second = get_or_create_ready_hosted_image(digest, 'https://x.invalid/b.jpg', data)

        self.assertTrue(uploaded_first)
        self.assertFalse(uploaded_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.upload.call_count, calls_after_first, 're-uploaded already-hosted content')
        self.assertEqual(HostedImage.objects.count(), 1)

    def test_every_ready_image_gets_an_upload_variant(self):
        data = make_jpeg(1200, 800)
        hosted, _ = get_or_create_ready_hosted_image(content_hash_for(data), 'https://x.invalid/a.jpg', data)

        self.assertTrue(hosted.upload_image.endswith('upload.jpg'))
        self.assertTrue(hosted.upload_url().endswith('upload.jpg'))
        self.assertEqual(hosted.status, HostedImage.STATUS_READY)

    def test_same_photo_at_two_resolutions_is_NOT_deduped(self):
        """Documents the remaining duplicate-image exposure.

        Content-hash dedup is byte-level, so if a scraper ever emits two
        renditions of one photo they become two HostedImages and the extension
        publishes the photo twice. Dedup therefore has to happen at parse time
        (issue 4's fix) — this test pins that fact down so nobody assumes the
        pipeline is a safety net for it.
        """
        big = make_jpeg(1600, 1067)
        small = make_jpeg(800, 533)
        self.assertNotEqual(content_hash_for(big), content_hash_for(small))

        get_or_create_ready_hosted_image(content_hash_for(big), 'https://x.invalid/full.jpg', big)
        get_or_create_ready_hosted_image(content_hash_for(small), 'https://x.invalid/thumb.jpg', small)
        self.assertEqual(HostedImage.objects.count(), 2)


class HostedUrlTests(SimpleTestCase):
    @override_settings(AWS_CLOUDFRONT_DOMAIN='images.test.invalid')
    def test_cdn_url_used_when_configured(self):
        self.assertEqual(public_url_for('vehicle-images/ab/hash/upload.jpg'),
                         'https://images.test.invalid/vehicle-images/ab/hash/upload.jpg')

    @override_settings(AWS_CLOUDFRONT_DOMAIN='', AWS_VEHICLE_IMAGE_BUCKET='b', AWS_VEHICLE_IMAGE_REGION='ap-southeast-2')
    def test_direct_s3_url_used_without_cdn(self):
        self.assertEqual(public_url_for('k'), 'https://b.s3.ap-southeast-2.amazonaws.com/k')

    def test_upload_key_sits_beside_the_webp_variants(self):
        digest = 'a' * 64
        self.assertEqual(s3_key_for(digest, 'upload', ext='jpg'),
                         f'vehicle-images/aa/{digest}/upload.jpg')
        self.assertEqual(s3_key_for(digest, 'large'), f'vehicle-images/aa/{digest}/large.webp')


# ─────────────────────────────────────────────────────────────────────────────
# Remediation of listings already published with duplicates
# ─────────────────────────────────────────────────────────────────────────────
class ResyncCustomDomainImagesCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='pablo2@test.invalid', password='x')
        self.profile = CustomDomainProfileListing.objects.create(
            url='https://easyvehiclesaustralia.com.au/stock', user=self.user,
            profile_id='easyvehiclesaustralia.com.au', status='completed',
        )
        # What the old parser stored: every photo twice.
        self.duplicated = [
            'https://storage.googleapis.com/au-assets/a_full.jpg',
            'https://storage.googleapis.com/au-assets/a_thumb.jpg',
            'https://storage.googleapis.com/au-assets/b_full.jpg',
            'https://storage.googleapis.com/au-assets/b_thumb.jpg',
        ]
        self.deduped = [
            'https://storage.googleapis.com/au-assets/a_full.jpg',
            'https://storage.googleapis.com/au-assets/b_full.jpg',
        ]
        self.listing = VehicleListing.objects.create(
            user=self.user, custom_domain_profile=self.profile, list_id='R1',
            seller_profile_id='easyvehiclesaustralia.com.au',
            url='https://easyvehiclesaustralia.com.au/buy/a-car/TOKEN',
            images=list(self.duplicated), status='completed', is_changed=False,
        )

    def _run(self, parsed_images, **options):
        adapter = mock.Mock()
        adapter.parse_listing.return_value = {'image': parsed_images} if parsed_images is not None else None
        with mock.patch('VehicleListing.management.commands.resync_custom_domain_images.resolve_for_url',
                        return_value=adapter), \
                mock.patch('VehicleListing.tasks.process_vehicle_listing_image_task.delay'):
            with self.captureOnCommitCallbacks(execute=True):
                call_command('resync_custom_domain_images', '--delay=0', stdout=io.StringIO(),
                             stderr=io.StringIO(), **options)
        self.listing.refresh_from_db()

    def test_duplicates_are_rewritten_and_flagged_for_republish(self):
        self._run(self.deduped)
        self.assertEqual(self.listing.images, self.deduped)
        self.assertTrue(self.listing.is_changed, 'extension will never republish this listing')
        self.assertEqual(
            list(self.listing.image_slots.order_by('position').values_list('source_url', flat=True)),
            self.deduped,
        )

    def test_dry_run_changes_nothing(self):
        self._run(self.deduped, dry_run=True)
        self.assertEqual(self.listing.images, self.duplicated)
        self.assertFalse(self.listing.is_changed)

    def test_empty_reparse_never_wipes_a_listings_photos(self):
        """A bot-challenge page parses to nothing — that must not blank a live
        listing's images."""
        self._run([])
        self.assertEqual(self.listing.images, self.duplicated)
        self.assertFalse(self.listing.is_changed)

        self._run(None)
        self.assertEqual(self.listing.images, self.duplicated)

    def test_already_correct_listing_is_left_alone(self):
        self.listing.images = list(self.deduped)
        self.listing.save(update_fields=['images'])

        self._run(self.deduped)
        self.assertFalse(self.listing.is_changed, 'triggered a pointless republish')

    def test_no_mark_changed_rewrites_without_republishing(self):
        self._run(self.deduped, no_mark_changed=True)
        self.assertEqual(self.listing.images, self.deduped)
        self.assertFalse(self.listing.is_changed)


# ─────────────────────────────────────────────────────────────────────────────
# Remediation of VehicleListingImage rows stuck FAILED by the Gumtree 403
# (the download-side fix above only helps NEW downloads — these rows need an
# explicit requeue since nothing else re-drives an already-FAILED slot).
# ─────────────────────────────────────────────────────────────────────────────
class RetryFailedVehicleImagesCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='retry@test.invalid', password='x')
        profile = GumtreeProfileListing.objects.create(user=self.user)
        self.listing = VehicleListing.objects.create(
            user=self.user, list_id='RT1', seller_profile_id='P', gumtree_profile=profile)

    def _make_slot(self, source_url, status=VehicleListingImage.STATUS_FAILED, retry_count=5):
        return VehicleListingImage.objects.create(
            listing=self.listing, source_url=source_url, status=status,
            retry_count=retry_count, error_message='403 Client Error: Forbidden for url: ' + source_url,
        )

    def test_only_failed_gumtree_cdn_rows_are_requeued(self):
        gumtree_failed = self._make_slot('https://images.gumtree.com.au/image/private/t_$_20/move/a')
        other_domain_failed = self._make_slot('https://storage.googleapis.com/au-assets/b.jpg')
        gumtree_ready = self._make_slot(
            'https://images.gumtree.com.au/image/private/t_$_20/move/c',
            status=VehicleListingImage.STATUS_READY, retry_count=0)

        with mock.patch('VehicleListing.management.commands.retry_failed_vehicle_images.'
                         'process_vehicle_listing_image_task.delay') as delay:
            call_command('retry_failed_vehicle_images', stdout=io.StringIO())

        delay.assert_called_once_with(gumtree_failed.pk)

        gumtree_failed.refresh_from_db()
        self.assertEqual(gumtree_failed.status, VehicleListingImage.STATUS_PENDING)
        self.assertEqual(gumtree_failed.retry_count, 0)
        self.assertIsNone(gumtree_failed.error_message)

        other_domain_failed.refresh_from_db()
        self.assertEqual(other_domain_failed.status, VehicleListingImage.STATUS_FAILED, 'touched a non-Gumtree row')

        gumtree_ready.refresh_from_db()
        self.assertEqual(gumtree_ready.status, VehicleListingImage.STATUS_READY, 'touched an already-ready row')

    def test_dry_run_changes_nothing(self):
        slot = self._make_slot('https://images.gumtree.com.au/image/private/t_$_20/move/a')

        with mock.patch('VehicleListing.management.commands.retry_failed_vehicle_images.'
                         'process_vehicle_listing_image_task.delay') as delay:
            call_command('retry_failed_vehicle_images', '--dry-run', stdout=io.StringIO())

        delay.assert_not_called()
        slot.refresh_from_db()
        self.assertEqual(slot.status, VehicleListingImage.STATUS_FAILED)

    def test_limit_caps_how_many_rows_are_requeued(self):
        for i in range(3):
            self._make_slot(f'https://images.gumtree.com.au/image/private/t_$_20/move/{i}')

        with mock.patch('VehicleListing.management.commands.retry_failed_vehicle_images.'
                         'process_vehicle_listing_image_task.delay') as delay:
            call_command('retry_failed_vehicle_images', '--limit=2', stdout=io.StringIO())

        self.assertEqual(delay.call_count, 2)
        self.assertEqual(
            VehicleListingImage.objects.filter(status=VehicleListingImage.STATUS_PENDING).count(), 2)


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: real dealer page → slots → what the extension receives
# ─────────────────────────────────────────────────────────────────────────────
class EasyVehiclesEndToEndTests(TestCase):
    @override_settings(EXTENSION_USE_HOSTED_IMAGES=True, EASYVEHICLES_VERIFY_IMAGE_URLS=False)
    def test_real_listing_publishes_each_photo_exactly_once(self):
        user = User.objects.create_user(email='pablo@test.invalid', password='x')
        request = RequestFactory().get('/api/vehicle-listing/custom-domain-listings/')

        for index, (fixture, label) in enumerate(LIVE_FIXTURES):
            with self.subTest(listing=label):
                urls = EasyVehiclesAustraliaAdapter()._parse_images(load_fixture(fixture))
                listing = VehicleListing.objects.create(
                    user=user, list_id=f'E2E{index}', seller_profile_id='easyvehiclesaustralia.com.au',
                    images=urls,
                )
                with mock.patch('VehicleListing.tasks.process_vehicle_listing_image_task.delay'):
                    _sync_listing_images(listing, urls)

                # Simulate the ingest worker finishing every slot.
                for slot in listing.image_slots.all():
                    hosted = HostedImage.objects.create(
                        content_hash=f'{index}{slot.pk:063d}',
                        large_image=f'vehicle-images/aa/{slot.pk}/large.webp',
                        upload_image=f'vehicle-images/aa/{slot.pk}/upload.jpg',
                        status=HostedImage.STATUS_READY,
                    )
                    slot.hosted_image = hosted
                    slot.status = VehicleListingImage.STATUS_READY
                    slot.save(update_fields=['hosted_image', 'status'])

                payload = _resolve_extension_images(listing, request)

                self.assertEqual(len(payload), len(urls), 'photo count changed between scrape and publish')
                self.assertEqual(len(set(payload)), len(payload), 'duplicate photo in the publish payload')
                self.assertGreaterEqual(len(payload), 15, 'below the extension minimum-image threshold')
                self.assertTrue(all(u.endswith('.jpg') for u in payload))
                self.assertFalse(any('custom-domain-image' in u for u in payload),
                                 'publish still depends on the live image proxy')


# ─────────────────────────────────────────────────────────────────────────────
# Facebook now publishes from OUR hosted copy for every listing source,
# Gumtree included — not just custom-domain. Per-photo fallback to the raw
# source URL is what keeps publishing unblocked for anything not yet hosted.
# ─────────────────────────────────────────────────────────────────────────────
class UploadVariantSourceScopingTests(TestCase):
    """The FB-safe JPEG upload variant is now built for EVERY listing source —
    Gumtree included — since Facebook is served our hosted copy for Gumtree
    listings too (see _resolve_extension_images). build_upload_variant remains
    a capability switch on the pipeline function itself (still exercised
    directly below), but the task no longer opts Gumtree out of it."""

    def setUp(self):
        self.user = User.objects.create_user(email='scope@test.invalid', password='x')
        patcher = mock.patch('VehicleListing.image_pipeline.upload_variant')
        self.upload = patcher.start()
        self.addCleanup(patcher.stop)

    def _jpeg_uploaded(self):
        return any(c.kwargs.get('content_type') == 'image/jpeg' for c in self.upload.mock_calls)

    # --- pipeline level: build_upload_variant is still a real, honoured switch ---
    def test_build_upload_variant_true_builds_the_jpeg(self):
        data = make_jpeg(1200, 800)
        hosted, _ = get_or_create_ready_hosted_image(
            content_hash_for(data), 'https://x/a.jpg', data, build_upload_variant=True)
        self.assertTrue(hosted.upload_image.endswith('upload.jpg'))
        self.assertTrue(self._jpeg_uploaded())

    def test_build_upload_variant_false_builds_no_jpeg(self):
        data = make_jpeg(1200, 800)
        hosted, _ = get_or_create_ready_hosted_image(
            content_hash_for(data), 'https://x/a.jpg', data, build_upload_variant=False)
        self.assertEqual(hosted.upload_image, '')
        self.assertIsNone(hosted.upload_url())
        self.assertFalse(self._jpeg_uploaded(), 'wasted a JPEG encode/upload when build_upload_variant=False')

    def test_jpeg_added_lazily_when_a_later_call_requests_it(self):
        data = make_jpeg(1200, 800)
        digest = content_hash_for(data)
        g, _ = get_or_create_ready_hosted_image(digest, 'https://x/a.jpg', data, build_upload_variant=False)
        self.assertEqual(g.upload_image, '')
        c, uploaded = get_or_create_ready_hosted_image(digest, 'https://x/a.jpg', data, build_upload_variant=True)
        self.assertFalse(uploaded, 'should be a dedup hit, not a new row')
        self.assertEqual(c.pk, g.pk)
        self.assertTrue(c.upload_image.endswith('upload.jpg'))
        self.assertEqual(HostedImage.objects.count(), 1)

    # --- task level: EVERY listing source now asks for the JPEG ---
    def _run_task(self, listing):
        slot = VehicleListingImage.objects.create(
            listing=listing, source_url='https://x/p.jpg', position=0)
        hosted = HostedImage.objects.create(
            content_hash='h' * 64, large_image='k/large.webp', status=HostedImage.STATUS_READY)
        with mock.patch('VehicleListing.tasks.download_image_bytes', return_value=make_jpeg(100, 100)), \
             mock.patch('VehicleListing.tasks.get_or_create_ready_hosted_image',
                        return_value=(hosted, True)) as gocr:
            process_vehicle_listing_image_task.apply(args=[slot.pk])
        return gocr.call_args

    def test_task_enables_upload_variant_for_gumtree(self):
        profile = GumtreeProfileListing.objects.create(user=self.user)
        listing = VehicleListing.objects.create(
            user=self.user, list_id='G1', seller_profile_id='P', gumtree_profile=profile)
        _args, kwargs = self._run_task(listing)
        self.assertTrue(kwargs['build_upload_variant'], 'Gumtree image did not ask for its JPEG variant')

    def test_task_enables_upload_variant_for_custom_domain(self):
        profile = CustomDomainProfileListing.objects.create(
            user=self.user, url='https://d.com/stock', profile_id='d.com', status='completed')
        listing = VehicleListing.objects.create(
            user=self.user, list_id='C1', seller_profile_id='d.com', custom_domain_profile=profile)
        _args, kwargs = self._run_task(listing)
        self.assertTrue(kwargs['build_upload_variant'], 'custom-domain image skipped its JPEG variant')


class ExtensionPayloadGumtreeGuardTests(TestCase):
    """Gumtree listings now ONLY ever publish OUR hosted JPEG — the raw Gumtree
    URL is never sent to the extension, at any point. A listing whose photos
    haven't all settled yet (still pending/processing) is withheld entirely;
    once everything has settled, permanently-Failed photos are dropped and the
    listing publishes with whatever is Ready."""

    def setUp(self):
        self.user = User.objects.create_user(email='gum@test.invalid', password='x')
        self.request = RequestFactory().get('/api/vehicle-listing/custom-domain-listings/')
        self.profile = GumtreeProfileListing.objects.create(user=self.user)

    def test_all_ready_publishes_only_our_hosted_jpegs(self):
        listing = VehicleListing.objects.create(
            user=self.user, list_id='G1', seller_profile_id='P', gumtree_profile=self.profile,
            images=['https://images.gumtree.com.au/a.jpg', 'https://images.gumtree.com.au/b.jpg'])
        for i, letter in enumerate('ab'):
            hosted = HostedImage.objects.create(
                content_hash=f'{i}' * 64, large_image='k/large.webp',
                upload_image=f'k/upload{i}.jpg', status=HostedImage.STATUS_READY)
            VehicleListingImage.objects.create(
                listing=listing, source_url=f'https://images.gumtree.com.au/{letter}.jpg',
                position=i, hosted_image=hosted, status=VehicleListingImage.STATUS_READY)

        payload = _resolve_extension_images(listing, self.request)

        self.assertEqual(len(payload), 2)
        self.assertTrue(all(u.endswith('.jpg') and 'gumtree' not in u for u in payload),
                         'a raw Gumtree URL leaked into the payload')

    def test_any_still_settling_photo_withholds_the_whole_listing(self):
        """Even one photo still pending/processing means nothing is published
        yet — never send a partial set early."""
        listing = VehicleListing.objects.create(
            user=self.user, list_id='G2', seller_profile_id='P', gumtree_profile=self.profile,
            images=['https://images.gumtree.com.au/a.jpg', 'https://images.gumtree.com.au/b.jpg'])
        hosted = HostedImage.objects.create(
            content_hash='9' * 64, large_image='k/large.webp',
            upload_image='k/upload.jpg', status=HostedImage.STATUS_READY)
        VehicleListingImage.objects.create(
            listing=listing, source_url='https://images.gumtree.com.au/a.jpg',
            position=0, hosted_image=hosted, status=VehicleListingImage.STATUS_READY)
        VehicleListingImage.objects.create(
            listing=listing, source_url='https://images.gumtree.com.au/b.jpg',
            position=1, status=VehicleListingImage.STATUS_PENDING)

        payload = _resolve_extension_images(listing, self.request)

        self.assertEqual(payload, [], 'published early while a photo was still settling')

    def test_processing_status_also_withholds_the_listing(self):
        listing = VehicleListing.objects.create(
            user=self.user, list_id='G3', seller_profile_id='P', gumtree_profile=self.profile,
            images=['https://images.gumtree.com.au/a.jpg'])
        VehicleListingImage.objects.create(
            listing=listing, source_url='https://images.gumtree.com.au/a.jpg',
            position=0, status=VehicleListingImage.STATUS_PROCESSING)

        self.assertEqual(_resolve_extension_images(listing, self.request), [])

    def test_permanently_failed_photo_is_dropped_once_everything_else_has_settled(self):
        """Once no slot is still pending/processing, a Failed one is dropped
        forever — it must not block the Ready photos from publishing."""
        listing = VehicleListing.objects.create(
            user=self.user, list_id='G4', seller_profile_id='P', gumtree_profile=self.profile,
            images=['https://images.gumtree.com.au/a.jpg', 'https://images.gumtree.com.au/b.jpg'])
        hosted = HostedImage.objects.create(
            content_hash='7' * 64, large_image='k/large.webp',
            upload_image='k/upload.jpg', status=HostedImage.STATUS_READY)
        VehicleListingImage.objects.create(
            listing=listing, source_url='https://images.gumtree.com.au/a.jpg',
            position=0, hosted_image=hosted, status=VehicleListingImage.STATUS_READY)
        VehicleListingImage.objects.create(
            listing=listing, source_url='https://images.gumtree.com.au/b.jpg',
            position=1, status=VehicleListingImage.STATUS_FAILED)

        payload = _resolve_extension_images(listing, self.request)

        self.assertEqual(len(payload), 1)
        self.assertTrue(payload[0].endswith('upload.jpg'))
        self.assertFalse(any('gumtree' in u for u in payload), 'raw Gumtree URL leaked into the payload')

    def test_gumtree_with_no_slots_yet_is_withheld_not_raw_fallback(self):
        """Legacy/just-scraped listing with no image_slots at all yet must NOT
        publish raw Gumtree URLs — wait for the pipeline to create and process
        slots instead."""
        listing = VehicleListing.objects.create(
            user=self.user, list_id='G5', seller_profile_id='P', gumtree_profile=self.profile,
            images=['https://images.gumtree.com.au/a.jpg', 'https://images.gumtree.com.au/b.jpg'])

        payload = _resolve_extension_images(listing, self.request)

        self.assertEqual(payload, [])

    def test_ready_hosted_image_missing_the_jpeg_variant_is_treated_as_not_ready(self):
        """Belt-and-braces: an old HostedImage predating the JPEG variant
        (upload_url() -> None) must be dropped, not somehow fall back raw."""
        listing = VehicleListing.objects.create(
            user=self.user, list_id='G6', seller_profile_id='P', gumtree_profile=self.profile,
            images=['https://images.gumtree.com.au/a.jpg'])
        hosted = HostedImage.objects.create(
            content_hash='6' * 64, large_image='k/large.webp',
            upload_image='', status=HostedImage.STATUS_READY)
        VehicleListingImage.objects.create(
            listing=listing, source_url='https://images.gumtree.com.au/a.jpg',
            position=0, hosted_image=hosted, status=VehicleListingImage.STATUS_READY)

        self.assertEqual(_resolve_extension_images(listing, self.request), [])

    @override_settings(EXTENSION_USE_HOSTED_IMAGES=False)
    def test_gumtree_hosted_only_policy_is_independent_of_the_custom_domain_kill_switch(self):
        """Gumtree must keep this policy even while custom-domain's own
        staged-rollout switch is off — the two are unrelated rollouts."""
        listing = VehicleListing.objects.create(
            user=self.user, list_id='G7', seller_profile_id='P', gumtree_profile=self.profile,
            images=['https://images.gumtree.com.au/a.jpg'])
        hosted = HostedImage.objects.create(
            content_hash='8' * 64, large_image='k/large.webp',
            upload_image='k/upload.jpg', status=HostedImage.STATUS_READY)
        VehicleListingImage.objects.create(
            listing=listing, source_url='https://images.gumtree.com.au/a.jpg',
            position=0, hosted_image=hosted, status=VehicleListingImage.STATUS_READY)

        payload = _resolve_extension_images(listing, self.request)

        self.assertTrue(payload[0].endswith('upload.jpg'))


class BackfillScopingTests(TestCase):
    """The one-off backfill now creates JPEGs for every ready hosted image
    missing one, regardless of listing source — Gumtree included."""

    def setUp(self):
        self.user = User.objects.create_user(email='bf@test.invalid', password='x')

    def _hosted(self, n):
        return HostedImage.objects.create(
            content_hash=f'{n:064d}', large_image=f'vehicle-images/aa/{n}/large.webp',
            upload_image='', status=HostedImage.STATUS_READY)

    def test_backfill_targets_every_source_including_gumtree(self):
        cd_profile = CustomDomainProfileListing.objects.create(
            user=self.user, url='https://d.com/stock', profile_id='d.com', status='completed')
        cd_listing = VehicleListing.objects.create(
            user=self.user, list_id='CD', seller_profile_id='d.com', custom_domain_profile=cd_profile)
        gum_profile = GumtreeProfileListing.objects.create(user=self.user)
        gum_listing = VehicleListing.objects.create(
            user=self.user, list_id='GU', seller_profile_id='P', gumtree_profile=gum_profile)

        cd_img, gum_img = self._hosted(1), self._hosted(2)
        VehicleListingImage.objects.create(listing=cd_listing, source_url='https://x/1.jpg', position=0, hosted_image=cd_img)
        VehicleListingImage.objects.create(listing=gum_listing, source_url='https://x/2.jpg', position=0, hosted_image=gum_img)

        with mock.patch('VehicleListing.management.commands.backfill_upload_variants._s3_client') as s3, \
             mock.patch('VehicleListing.management.commands.backfill_upload_variants.upload_variant'):
            # A fresh BytesIO per call — a shared one would be exhausted (read to
            # EOF) by the first of the two HostedImages processed in this run,
            # leaving the second with an empty stream and a silent per-item failure.
            s3.return_value.get_object.side_effect = (
                lambda **kwargs: {'Body': io.BytesIO(make_jpeg(1600, 1067))}
            )
            call_command('backfill_upload_variants', stdout=io.StringIO(), stderr=io.StringIO())

        cd_img.refresh_from_db()
        gum_img.refresh_from_db()
        self.assertTrue(cd_img.upload_image.endswith('upload.jpg'), 'custom-domain image was not backfilled')
        self.assertTrue(gum_img.upload_image.endswith('upload.jpg'), 'Gumtree image was not backfilled')
