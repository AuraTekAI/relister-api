"""Regression tests for VehicleListing API endpoints."""
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .gumtree_scraper import ensure_gumtree_profile_placeholder
from .models import GumtreeProfileListing, VehicleListing


class GetUserGumtreeProfileVehicleListingsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='gumtree@test.invalid', password='x')
        self.url_with_slash = 'https://www.gumtree.com.au/web/s-user/1499778731277/'
        self.url_without_slash = 'https://www.gumtree.com.au/web/s-user/1499778731277'
        self.profile = GumtreeProfileListing.objects.create(
            user=self.user,
            url=self.url_with_slash,
            profile_id='1499778731277',
        )
        self.listing = VehicleListing.objects.create(
            user=self.user,
            gumtree_profile=self.profile,
            list_id='GT1',
            seller_profile_id='1499778731277',
        )
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

    def _get(self, url):
        return self.client.get(
            reverse('get_user_gumtree_profile_vehicle_listings'),
            {'url': url},
        )

    def test_lookup_by_profile_id_survives_trailing_slash_difference(self):
        """The extension may request the profile URL without the trailing slash
        that was stored during onboarding; the endpoint should still find it."""
        response = self._get(self.url_without_slash)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['list_id'], 'GT1')

    def test_lookup_with_exactly_stored_url(self):
        response = self._get(self.url_with_slash)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)

    def test_missing_url_parameter_returns_400(self):
        response = self.client.get(reverse('get_user_gumtree_profile_vehicle_listings'))
        self.assertEqual(response.status_code, 400)

    def test_invalid_gumtree_url_returns_400(self):
        response = self._get('https://www.gumtree.com.au/web/s-user/not-a-number/')
        self.assertEqual(response.status_code, 400)

    def test_other_users_profile_is_not_accessible(self):
        other_user = User.objects.create_user(email='other@test.invalid', password='x')
        token = RefreshToken.for_user(other_user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        response = self._get(self.url_without_slash)
        self.assertEqual(response.status_code, 404)


class EnsureGumtreeProfilePlaceholderTests(TestCase):
    """Regression coverage for the approval-time placeholder row that closes
    the race between the extension's GET (right after login/approval) and the
    async profile_listings_for_approved_users Celery task."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='pending-gumtree@test.invalid',
            password='x',
            gumtree_dealarship_url='https://www.gumtree.com.au/web/s-user/1499778731277/',
        )

    def _get(self, url):
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
        return self.client.get(
            reverse('get_user_gumtree_profile_vehicle_listings'),
            {'url': url},
        )

    def test_placeholder_lets_endpoint_find_profile_before_celery_runs(self):
        """Without the placeholder this would 404 — no GumtreeProfileListing row
        exists yet since the Celery task hasn't run/completed."""
        response = self._get(self.user.gumtree_dealarship_url)
        self.assertEqual(response.status_code, 404)

        ensure_gumtree_profile_placeholder(self.user)

        response = self._get(self.user.gumtree_dealarship_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 0)

    def test_placeholder_is_pending_and_not_duplicated(self):
        instance = ensure_gumtree_profile_placeholder(self.user)
        self.assertEqual(instance.status, 'pending')
        self.assertEqual(
            GumtreeProfileListing.objects.filter(user=self.user).count(), 1
        )

        # Calling it again (e.g. re-approval) must not create a second row.
        same_instance = ensure_gumtree_profile_placeholder(self.user)
        self.assertEqual(same_instance.pk, instance.pk)
        self.assertEqual(
            GumtreeProfileListing.objects.filter(user=self.user).count(), 1
        )

    def test_no_gumtree_url_is_a_noop(self):
        user = User.objects.create_user(email='no-gumtree@test.invalid', password='x')
        self.assertIsNone(ensure_gumtree_profile_placeholder(user))
        self.assertFalse(GumtreeProfileListing.objects.filter(user=user).exists())
