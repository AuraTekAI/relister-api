"""Tests for User.dealer_facebook_profile — the append-only, deduped list of
Facebook dealership accounts reported by the extension at login.

Covers the required scenarios:
  1. New user + first Facebook dealership account.
  2. Existing user + same account  → no duplicate.
  3. Existing user + different account → appended.
  4. Existing user with no stored accounts → list created correctly.
  5. Data persists in the database (re-fetched, not just in-memory).
Plus: the login endpoint captures the field, and a malformed value never
breaks a login.
"""
from django.test import TestCase
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

    def test_different_account_appended(self):
        user = make_user('fbp-append@example.com')
        user.add_dealer_facebook_profile('facebook_account_1')
        changed = user.add_dealer_facebook_profile('facebook_account_2')
        self.assertTrue(changed)
        user.refresh_from_db()
        self.assertEqual(
            user.dealer_facebook_profile,
            ['facebook_account_1', 'facebook_account_2'],
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

    def test_list_input_appended_uniquely(self):
        user = make_user('fbp-list@example.com')
        user.add_dealer_facebook_profile(['facebook_account_1', 'facebook_account_2'])
        user.add_dealer_facebook_profile(['facebook_account_2', 'facebook_account_3'])
        user.refresh_from_db()
        self.assertEqual(
            user.dealer_facebook_profile,
            ['facebook_account_1', 'facebook_account_2', 'facebook_account_3'],
        )


class LoginCaptureTests(TestCase):
    def setUp(self):
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

    def test_second_login_different_account_appends(self):
        self.login(dealer_facebook_profile='facebook_account_1')
        self.login(dealer_facebook_profile='facebook_account_2')
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
