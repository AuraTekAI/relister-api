"""Tests for User.dealer_facebook_profile — the deduped, most-recently-seen-first
list of Facebook dealership accounts reported by the extension at login.

Covers the required scenarios:
  1. New user + first Facebook dealership account.
  2. Existing user + same account  → no duplicate.
  3. Existing user + different account → stored at the top.
  4. Existing user + already-known account → moved back to the top, not re-inserted.
  5. Existing user with no stored accounts → list created correctly.
  6. Data persists in the database (re-fetched, not just in-memory).
Plus: the login endpoint captures the field, and a malformed value never
breaks a login.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User


def make_user(email, password='Str0ngPass!x', **extra):
    user = User.objects.create_user(email=email, password=password, **extra)
    user.is_approved = True
    user.is_active = True
    user.save()
    return user


class AddDealerFacebookProfileTests(TestCase):
    def test_first_account_on_new_user(self):
        user = make_user('fbp-new@example.com')
        changed = user.add_dealer_facebook_profile('facebook_account_1')
        self.assertTrue(changed)
        user.refresh_from_db()
        self.assertEqual(user.dealer_facebook_profile, ['facebook_account_1'])

    def test_same_account_not_duplicated(self):
        user = make_user('fbp-dup@example.com')
        user.add_dealer_facebook_profile('facebook_account_1')
        changed = user.add_dealer_facebook_profile('facebook_account_1')
        self.assertFalse(changed)
        user.refresh_from_db()
        self.assertEqual(user.dealer_facebook_profile, ['facebook_account_1'])

    def test_different_account_goes_to_top(self):
        user = make_user('fbp-append@example.com')
        user.add_dealer_facebook_profile('facebook_account_1')
        changed = user.add_dealer_facebook_profile('facebook_account_2')
        self.assertTrue(changed)
        user.refresh_from_db()
        self.assertEqual(
            user.dealer_facebook_profile,
            ['facebook_account_2', 'facebook_account_1'],
        )

    def test_known_account_moves_back_to_top(self):
        # The account is already registered, so it must not be inserted a
        # second time — it just returns to the top of the list.
        user = make_user('fbp-move@example.com')
        user.add_dealer_facebook_profile('facebook_account_1')
        user.add_dealer_facebook_profile('facebook_account_2')
        user.add_dealer_facebook_profile('facebook_account_3')
        changed = user.add_dealer_facebook_profile('facebook_account_1')
        self.assertTrue(changed)
        user.refresh_from_db()
        self.assertEqual(
            user.dealer_facebook_profile,
            ['facebook_account_1', 'facebook_account_3', 'facebook_account_2'],
        )

    def test_account_already_on_top_is_a_no_op(self):
        user = make_user('fbp-noop@example.com')
        user.add_dealer_facebook_profile('facebook_account_1')
        user.add_dealer_facebook_profile('facebook_account_2')
        changed = user.add_dealer_facebook_profile('facebook_account_2')
        self.assertFalse(changed)
        user.refresh_from_db()
        self.assertEqual(
            user.dealer_facebook_profile,
            ['facebook_account_2', 'facebook_account_1'],
        )

    def test_existing_user_with_no_stored_accounts(self):
        # A pre-existing row that has never stored an account holds [] (the
        # column is NOT NULL with default=list, so legacy rows are backfilled
        # by the migration default). First append must create the list.
        user = make_user('fbp-legacy@example.com')
        self.assertEqual(user.dealer_facebook_profile, [])
        changed = user.add_dealer_facebook_profile('facebook_account_1')
        self.assertTrue(changed)
        user.refresh_from_db()
        self.assertEqual(user.dealer_facebook_profile, ['facebook_account_1'])

    def test_blank_and_empty_values_ignored(self):
        user = make_user('fbp-blank@example.com')
        self.assertFalse(user.add_dealer_facebook_profile(''))
        self.assertFalse(user.add_dealer_facebook_profile(None))
        self.assertFalse(user.add_dealer_facebook_profile(['  ', '']))
        user.refresh_from_db()
        self.assertEqual(user.dealer_facebook_profile, [])

    def test_list_input_stored_newest_first_uniquely(self):
        # A list is taken as already newest-first: it keeps the order given and
        # lands on top, with the previously stored accounts following.
        user = make_user('fbp-list@example.com')
        user.add_dealer_facebook_profile(['facebook_account_1', 'facebook_account_2'])
        user.add_dealer_facebook_profile(['facebook_account_2', 'facebook_account_3'])
        user.refresh_from_db()
        self.assertEqual(
            user.dealer_facebook_profile,
            ['facebook_account_2', 'facebook_account_3', 'facebook_account_1'],
        )


# /api/login/ is throttled at 5/minute per IP and DRF keeps that history in the
# default cache, which outlives a single test. Point these tests at their own
# locmem cache and wipe it per test so one test's logins can't throttle the next
# (and so clearing it never touches a shared Redis).
@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'fbp-login-tests',
}})
class LoginCaptureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.password = 'Str0ngPass!x'
        self.user = make_user('fbp-login@example.com', password=self.password)

    def login(self, **extra_body):
        return self.client.post(
            '/api/login/',
            {'email': self.user.email, 'password': self.password, **extra_body},
            format='json',
        )

    def test_login_stores_reported_account(self):
        resp = self.login(dealer_facebook_profile='facebook_account_1')
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.dealer_facebook_profile, ['facebook_account_1'])

    def test_second_login_same_account_no_duplicate(self):
        self.login(dealer_facebook_profile='facebook_account_1')
        self.login(dealer_facebook_profile='facebook_account_1')
        self.user.refresh_from_db()
        self.assertEqual(self.user.dealer_facebook_profile, ['facebook_account_1'])

    def test_second_login_different_account_goes_to_top(self):
        self.login(dealer_facebook_profile='facebook_account_1')
        self.login(dealer_facebook_profile='facebook_account_2')
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.dealer_facebook_profile,
            ['facebook_account_2', 'facebook_account_1'],
        )

    def test_login_with_known_account_moves_it_to_top(self):
        self.login(dealer_facebook_profile='facebook_account_1')
        self.login(dealer_facebook_profile='facebook_account_2')
        self.login(dealer_facebook_profile='facebook_account_1')
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.dealer_facebook_profile,
            ['facebook_account_1', 'facebook_account_2'],
        )

    def test_login_without_field_still_works(self):
        resp = self.login()
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.dealer_facebook_profile, [])

    def test_malformed_value_never_breaks_login(self):
        resp = self.login(dealer_facebook_profile={'nested': {'junk': True}})
        self.assertEqual(resp.status_code, 200)
