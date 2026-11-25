"""Dealer Facebook account events (restricted / checkpoint / logged_out):
stored with dealer + issue type + exact timestamp; Kaz emailed once per open
issue via the central sender; [RESOLVED] on a "resolved" report.

Run: python src/manage.py test team_alerts.tests_account_events
"""
import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from team_alerts import alerting
from team_alerts.models import FacebookAccountEvent

KAZ = ['kazp@citysubaru.com.au']


class FacebookAccountEventTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(KAZ_ONLY=KAZ, ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'), ALERT_REMIND_HOURS=24)
        ctx.enable()
        self.addCleanup(ctx.disable)
        self.dealer = User.objects.create_user(email='dealer@example.com', password='x', dealership_name='Motoroo')
        self.client = APIClient()
        self.client.force_authenticate(user=self.dealer)
        self.url = reverse('team_alerts:fb_account_event')

    def _post(self, body, send_side_effect=None):
        with mock.patch.object(alerting, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            email_cls.return_value.send.side_effect = send_side_effect
            r = self.client.post(self.url, data=body, format='json')
        return r, email_cls

    def test_detected_stores_dealer_type_timestamp_and_alerts_kaz_once(self):
        r, email = self._post({
            'issue_type': 'checkpoint', 'status': 'detected', 'detected_at': '2026-11-20T01:02:03.456Z',
            'detail': 'redirected to checkpoint', 'fb_user_id': '100', 'extension_version': '1.2.56', 'dealer_id': self.dealer.id,
        })
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()['alerted'])
        ev = FacebookAccountEvent.objects.get()
        self.assertEqual((ev.user_id, ev.issue_type, ev.status), (self.dealer.id, 'checkpoint', 'detected'))
        self.assertEqual(ev.detected_at.isoformat(), '2026-11-20T01:02:03.456000+00:00')
        self.assertTrue(ev.alerted)
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], KAZ)
        self.assertEqual(kwargs['subject'], '[ALERT] Facebook security checkpoint — Motoroo')
        self.assertIn('2026-11-20 01:02:03 UTC', kwargs['body'])
        email.return_value.send.assert_called_once_with(fail_silently=False)

        # Same ongoing issue reported again → stored, not emailed.
        r2, email2 = self._post({'issue_type': 'checkpoint', 'detected_at': '2026-11-20T02:00:00Z'})
        self.assertTrue(r2.json()['suppressed'])
        self.assertFalse(FacebookAccountEvent.objects.get(id=r2.json()['event_id']).alerted)
        email2.assert_not_called()
        self.assertEqual(FacebookAccountEvent.objects.count(), 2)

    def test_resolved_sends_resolved_email_then_detected_again_alerts_again(self):
        self._post({'issue_type': 'logged_out', 'detected_at': '2026-11-20T01:00:00Z'})
        r, email = self._post({'issue_type': 'logged_out', 'status': 'resolved', 'detected_at': '2026-11-20T03:00:00Z'})
        self.assertEqual(r.status_code, 201)
        email.assert_called_once()
        self.assertEqual(email.call_args.kwargs['subject'], '[RESOLVED] Logged out of Facebook — Motoroo')
        _, email = self._post({'issue_type': 'logged_out', 'detected_at': '2026-11-20T05:00:00Z'})
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[ALERT] Logged out of Facebook'))

    def test_resolved_without_open_issue_sends_nothing(self):
        _, email = self._post({'issue_type': 'restricted', 'status': 'resolved'})
        email.assert_not_called()

    def test_different_issue_types_alert_independently(self):
        _, e1 = self._post({'issue_type': 'restricted'})
        _, e2 = self._post({'issue_type': 'logged_out'})
        e1.assert_called_once()
        e2.assert_called_once()

    def test_failed_send_leaves_issue_unalerted_for_retry(self):
        r, _ = self._post({'issue_type': 'restricted'}, send_side_effect=Exception('flashpost 500'))
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.json()['alerted'])
        self.assertFalse(FacebookAccountEvent.objects.get().alerted)
        _, email = self._post({'issue_type': 'restricted'})
        email.assert_called_once()

    def test_validation(self):
        r, _ = self._post({'issue_type': 'banned'})
        self.assertEqual(r.status_code, 400)
        r, _ = self._post({'issue_type': 'restricted', 'status': 'maybe'})
        self.assertEqual(r.status_code, 400)
        r, _ = self._post({'issue_type': 'restricted', 'dealer_id': self.dealer.id + 999})
        self.assertEqual(r.status_code, 400)

    def test_requires_auth(self):
        r = APIClient().post(self.url, data={'issue_type': 'restricted'}, format='json')
        self.assertIn(r.status_code, (401, 403))

    def test_millisecond_epoch_timestamp_accepted(self):
        r, _ = self._post({'issue_type': 'restricted', 'detected_at': 1763600000000})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(FacebookAccountEvent.objects.get().detected_at.year, 2025)
