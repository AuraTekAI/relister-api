"""Failure-spike monitor: publish / delete / form-fill events vs the 7-day norm,
one Kaz-only email per spike via the central sender, [RESOLVED] when normal.

Run: python src/manage.py test team_alerts.tests_failure_monitor
"""
import json
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from team_alerts import alerting
from team_alerts import failure_monitor as fm
from team_alerts.models import FailureEvent
from VehicleListing.models import VehicleListing

KAZ = ['kazp@citysubaru.com.au']


class FailureSpikeMonitorTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'), ALERT_REMIND_HOURS=24,
            FAILURE_SPIKE_WINDOW_MINUTES=60, FAILURE_SPIKE_BASELINE_DAYS=7,
            FAILURE_SPIKE_MIN_COUNT=5, FAILURE_SPIKE_FACTOR=3.0,
            KAZ_ONLY=KAZ,
        )
        ctx.enable()
        self.addCleanup(ctx.disable)
        self.dealer = User.objects.create_user(email='dealer@example.com', password='x', dealership_name='Motoroo')
        self.other = User.objects.create_user(email='other@example.com', password='x')

    def _events(self, kind, n, minutes_ago=5, user=None, reason='x'):
        at = timezone.now() - timedelta(minutes=minutes_ago)
        for _ in range(n):
            FailureEvent.objects.create(user=user or self.dealer, kind=kind, reason=reason, occurred_at=at)

    def _run(self, send_side_effect=None):
        with mock.patch.object(alerting, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            email_cls.return_value.send.side_effect = send_side_effect
            result = fm.failure_spike_monitor()
        return result, email_cls

    def test_quiet_hour_sends_nothing(self):
        self._events('publish', 2)
        _, email = self._run()
        email.assert_not_called()

    def test_spike_with_no_history_alerts_kaz_only_once(self):
        self._events('publish', 6, reason="the model 'X' could not be filled on Facebook's form")
        self._events('publish', 3, user=self.other, reason='Facebook never confirmed the publish')
        result, email = self._run()
        self.assertTrue(result['publish'].startswith('SPIKE'))
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], KAZ)
        self.assertIn('Spike in publish failures: 9', kwargs['subject'])
        self.assertIn('Motoroo (id', kwargs['body'])
        email.return_value.send.assert_called_once_with(fail_silently=False)
        _, email2 = self._run()  # still spiking → no repeat
        email2.assert_not_called()

    def test_busy_kind_needs_factor_times_baseline(self):
        # 7 days × 24 windows = 168 windows; ~334 past failures → baseline ≈ 2/window.
        for h in range(2, 170):
            self._events('delete', 2, minutes_ago=h * 60)
        self._events('delete', 5)  # 5 < 3 × 2 → normal
        _, email = self._run()
        email.assert_not_called()
        self._events('delete', 2)  # 7 ≥ 6 → spike
        _, email = self._run()
        email.assert_called_once()
        self.assertIn('delete failures', email.call_args.kwargs['subject'])

    def test_recovery_resolves_and_rearms(self):
        self._events('form_fill', 6)
        self._run()
        FailureEvent.objects.all().delete()  # window quiet again
        _, email = self._run()
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED] Spike in Facebook form-fill failures'))
        self._events('form_fill', 6)
        _, email = self._run()
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[ALERT] Spike in Facebook form-fill failures'))

    def test_failed_send_is_retried_next_run(self):
        self._events('publish', 6)
        self._run(send_side_effect=Exception('flashpost 500'))
        _, email = self._run()
        email.assert_called_once()

    def test_classify_form_fill_from_reason_or_explicit_kind(self):
        self.assertEqual(fm.classify_publish_failure_kind("the model 'Forester' could not be filled on Facebook's form"), 'form_fill')
        self.assertEqual(fm.classify_publish_failure_kind("Facebook's form still had empty required field(s)"), 'form_fill')
        self.assertEqual(fm.classify_publish_failure_kind('filling the Facebook form took longer than 8 minutes'), 'form_fill')
        self.assertEqual(fm.classify_publish_failure_kind('Facebook never confirmed the publish'), 'publish')
        self.assertEqual(fm.classify_publish_failure_kind('anything', explicit_kind='form_fill'), 'form_fill')
        self.assertEqual(fm.classify_publish_failure_kind(None), 'publish')


class FailureEventRecordingTests(TestCase):
    def setUp(self):
        self.dealer = User.objects.create_user(email='dealer@example.com', password='x')
        self.listing = VehicleListing.objects.create(user=self.dealer, list_id='L1', seller_profile_id='S1')
        self.client = APIClient()
        self.client.force_authenticate(user=self.dealer)

    def test_publish_failure_post_writes_timeline_row(self):
        url = reverse('publish_failures')
        self.client.post(url, data=json.dumps({'id': self.listing.id, 'reason': "the make 'X' could not be filled on Facebook's form"}), content_type='application/json')
        self.client.post(url, data=json.dumps({'id': self.listing.id, 'reason': 'tab closed', 'kind': 'publish'}), content_type='application/json')
        kinds = list(FailureEvent.objects.filter(user=self.dealer).order_by('id').values_list('kind', flat=True))
        self.assertEqual(kinds, ['form_fill', 'publish'])
        self.assertEqual(FailureEvent.objects.first().listing_id, self.listing.id)

    def test_history_import_is_not_a_new_failure(self):
        self.client.post(reverse('publish_failures'), data=json.dumps({'id': self.listing.id, 'at_least_fail_count': 2}), content_type='application/json')
        self.assertEqual(FailureEvent.objects.count(), 0)

    def test_extension_reports_delete_failure(self):
        r = self.client.post(reverse('team_alerts:failure_event'), data={
            'kind': 'delete', 'listing_id': self.listing.id, 'reason': 'field_exception', 'occurred_at': '2026-11-20T03:04:05.000Z',
        }, format='json')
        self.assertEqual(r.status_code, 201)
        ev = FailureEvent.objects.get()
        self.assertEqual((ev.kind, ev.listing_id, ev.reason), ('delete', self.listing.id, 'field_exception'))
        self.assertEqual(ev.occurred_at.isoformat(), '2026-11-20T03:04:05+00:00')

    def test_unknown_kind_rejected(self):
        r = self.client.post(reverse('team_alerts:failure_event'), data={'kind': 'nope'}, format='json')
        self.assertEqual(r.status_code, 400)
