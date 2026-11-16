"""Admin edits an expired trial's end date into the future → status goes back to trial.

Run: python src/manage.py test accounts.tests_trial_reactivation
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User


class TrialReactivationOnEndDateExtensionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(email='admin@example.com', password='x')
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.user = User.objects.create_user(
            email='dealer@example.com', password='x', is_active=True,
            account_status='trial_expired',
            trial_start_date=timezone.now() - timedelta(days=40),
            trial_end_date=timezone.now() - timedelta(days=10),
        )
        self.url = reverse('users_detail', args=[self.user.id])

    def _patch(self, body):
        body = {'is_active': True, 'email': self.user.email, **body}
        return self.client.patch(self.url, data=body, format='json')

    def test_future_end_date_moves_expired_user_back_to_trial(self):
        new_end = timezone.now() + timedelta(days=14)
        r = self._patch({'trial_end_date': new_end.isoformat()})
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, 'trial')
        self.assertEqual(self.user.trial_end_date.replace(microsecond=0), new_end.replace(microsecond=0))
        # The response the webapp refreshes from already carries the new status.
        self.assertEqual(r.json()['data']['account_status'], 'trial')

    def test_past_end_date_keeps_user_expired(self):
        still_past = timezone.now() - timedelta(days=2)
        r = self._patch({'trial_end_date': still_past.isoformat()})
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, 'trial_expired')

    def test_explicit_status_in_same_patch_wins(self):
        new_end = timezone.now() + timedelta(days=14)
        r = self._patch({'trial_end_date': new_end.isoformat(), 'account_status': 'active'})
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, 'active')

    def test_non_expired_user_status_untouched(self):
        self.user.account_status = 'active'
        self.user.save(update_fields=['account_status'])
        r = self._patch({'trial_end_date': (timezone.now() + timedelta(days=30)).isoformat()})
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, 'active')

    def test_other_field_edit_does_not_change_expired_status(self):
        r = self._patch({'first_name': 'Sam'})
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, 'trial_expired')
