"""Lazy (publish-time) image ingestion tests.

The pipeline no longer downloads/stores photos to S3 when a profile is
scraped — scrape time only records VehicleListingImage slots. A listing's
photos are ingested on demand, for that ONE listing, when the extension is
about to publish it (GET /api/vehicle-listing/listing/<id>/images-status/).

Run with:
    python src/manage.py test VehicleListing.tests_lazy_image_ingest --settings=relister.settings_test
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import User

from .image_pipeline import ensure_listing_image_ingest, sync_listing_images
from .models import HostedImage, VehicleListing, VehicleListingImage
from .views import get_listing_images_status

TASK_PATH = 'VehicleListing.tasks.process_vehicle_listing_image_task.apply_async'


def make_listing(user, **overrides):
    defaults = dict(list_id='L1', seller_profile_id='P1', status='pending', images=[])
    defaults.update(overrides)
    return VehicleListing.objects.create(user=user, **defaults)


class ScrapeTimeIsLazyTests(TestCase):
    """Default behaviour: scraping records slots but enqueues NOTHING."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.listing = make_listing(self.user)

    def test_scrape_creates_slots_but_never_enqueues(self):
        urls = [f'https://x.invalid/p{i}.jpg' for i in range(20)]
        with mock.patch(TASK_PATH) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                sync_listing_images(self.listing, urls)

        self.assertEqual(self.listing.image_slots.count(), 20)
        self.assertTrue(all(
            s.status == VehicleListingImage.STATUS_PENDING
            for s in self.listing.image_slots.all()
        ))
        self.assertEqual(enqueue.call_count, 0,
                         'scrape must not start any S3 ingest under the lazy pipeline')

    def test_fifty_products_zero_upfront_ingest(self):
        # The motivating scenario: 50 products x 20 photos used to enqueue
        # ~1,000 download/upload tasks at scrape time. Now: zero.
        with mock.patch(TASK_PATH) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                for n in range(50):
                    listing = make_listing(self.user, list_id=f'car-{n}')
                    sync_listing_images(listing, [f'https://x.invalid/{n}/p{i}.jpg' for i in range(20)])
        self.assertEqual(VehicleListingImage.objects.count(), 1000)
        self.assertEqual(enqueue.call_count, 0)


class EnsureListingImageIngestTests(TestCase):
    """The publish-time trigger: queues exactly one listing's photos, once."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.listing = make_listing(self.user)
        for i in range(3):
            VehicleListingImage.objects.create(
                listing=self.listing, source_url=f'https://x.invalid/p{i}.jpg', position=i)

    def test_queues_all_pending_slots_for_this_listing_only(self):
        other = make_listing(self.user, list_id='L2')
        VehicleListingImage.objects.create(listing=other, source_url='https://x.invalid/other.jpg')

        with mock.patch(TASK_PATH) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                queued = ensure_listing_image_ingest(self.listing)

        self.assertEqual(queued, 3)
        self.assertEqual(enqueue.call_count, 3)
        self.assertEqual(
            self.listing.image_slots.filter(status=VehicleListingImage.STATUS_QUEUED).count(), 3)
        # The other listing's photo is untouched — one product at a time.
        self.assertEqual(other.image_slots.get().status, VehicleListingImage.STATUS_PENDING)

    def test_second_call_is_a_noop_while_ingest_is_in_flight(self):
        with mock.patch(TASK_PATH):
            with self.captureOnCommitCallbacks(execute=True):
                ensure_listing_image_ingest(self.listing)
        with mock.patch(TASK_PATH) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                queued = ensure_listing_image_ingest(self.listing)
        self.assertEqual(queued, 0)
        self.assertEqual(enqueue.call_count, 0, 'polling must never double-enqueue')

    def test_stale_queued_slots_are_reclaimed(self):
        # A task lost to a worker restart must not block the listing forever.
        with mock.patch(TASK_PATH):
            with self.captureOnCommitCallbacks(execute=True):
                ensure_listing_image_ingest(self.listing)
        stale = timezone.now() - timedelta(hours=2)
        VehicleListingImage.objects.filter(listing=self.listing).update(updated_at=stale)

        with mock.patch(TASK_PATH) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                queued = ensure_listing_image_ingest(self.listing)
        self.assertEqual(queued, 3)
        self.assertEqual(enqueue.call_count, 3)

    def test_terminal_slots_are_never_requeued(self):
        hosted = HostedImage.objects.create(
            content_hash='a' * 64, large_image='k/large.webp', status=HostedImage.STATUS_READY)
        self.listing.image_slots.filter(position=0).update(
            status=VehicleListingImage.STATUS_READY, hosted_image=hosted)
        self.listing.image_slots.filter(position=1).update(
            status=VehicleListingImage.STATUS_FAILED)

        with mock.patch(TASK_PATH) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                queued = ensure_listing_image_ingest(self.listing)
        self.assertEqual(queued, 1)  # only the remaining pending slot
        self.assertEqual(enqueue.call_count, 1)


@override_settings(EXTENSION_USE_HOSTED_IMAGES=True)
class ImagesStatusEndpointTests(TestCase):
    """GET /api/vehicle-listing/listing/<id>/images-status/ — the extension's
    pre-publish hook: triggers the ingest and reports completion."""

    def setUp(self):
        self.user = User.objects.create_user(email='dealer@test.invalid', password='x')
        self.listing = make_listing(self.user)
        self.factory = APIRequestFactory()

    def _get(self, listing_id, user=None):
        request = self.factory.get(f'/api/vehicle-listing/listing/{listing_id}/images-status/')
        force_authenticate(request, user=user or self.user)
        response = get_listing_images_status(request, listing_id)
        return response.status_code, json.loads(response.content)

    def test_first_call_queues_the_ingest_and_reports_in_flight(self):
        for i in range(2):
            VehicleListingImage.objects.create(
                listing=self.listing, source_url=f'https://x.invalid/p{i}.jpg', position=i)

        with mock.patch(TASK_PATH) as enqueue:
            # The fan-out defers to transaction.on_commit, which never fires
            # inside TestCase's atomic wrapper — execute it explicitly so the
            # enqueue assertion means something.
            with self.captureOnCommitCallbacks(execute=True):
                status_code, data = self._get(self.listing.id)

        self.assertEqual(status_code, 200)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(data['queued_now'], 2)
        self.assertEqual(data['in_flight'], 2)
        self.assertFalse(data['ingest_complete'])

    def test_complete_when_every_slot_is_terminal(self):
        hosted = HostedImage.objects.create(
            content_hash='b' * 64, large_image='k/large.webp',
            upload_image='k/upload.jpg', status=HostedImage.STATUS_READY)
        VehicleListingImage.objects.create(
            listing=self.listing, source_url='https://x.invalid/p0.jpg', position=0,
            status=VehicleListingImage.STATUS_READY, hosted_image=hosted)
        VehicleListingImage.objects.create(
            listing=self.listing, source_url='https://x.invalid/p1.jpg', position=1,
            status=VehicleListingImage.STATUS_FAILED)

        status_code, data = self._get(self.listing.id)
        self.assertEqual(status_code, 200)
        self.assertTrue(data['ingest_complete'])
        self.assertEqual(data['ready'], 1)
        self.assertEqual(data['failed'], 1)
        self.assertEqual(len(data['images']), 2)  # hosted copy + source fallback

    def test_listing_with_no_slots_is_immediately_complete(self):
        # e.g. hosting bypassed for this source — the extension must not block.
        status_code, data = self._get(self.listing.id)
        self.assertEqual(status_code, 200)
        self.assertTrue(data['ingest_complete'])
        self.assertEqual(data['total'], 0)

    def test_other_users_listing_is_404(self):
        other = User.objects.create_user(email='other@test.invalid', password='x')
        status_code, _data = self._get(self.listing.id, user=other)
        self.assertEqual(status_code, 404)
