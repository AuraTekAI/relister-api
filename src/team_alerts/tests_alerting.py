"""Central alert sender: first / reminder / escalation / resolved lifecycle,
recipient groups, support@ suppression, failed delivery never recorded,
weekly test email.

Run: python src/manage.py test team_alerts.tests_alerting
"""
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from team_alerts import alerting

KAZ = ['kazp@citysubaru.com.au']
TEAM = ['kazp@citysubaru.com.au', 'dev@example.com']


class AlertingTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'),
            ALERT_REMIND_HOURS=24, KAZ_ONLY=KAZ, KAZ_AND_TEAM=TEAM,
        )
        ctx.enable()
        self.addCleanup(ctx.disable)

    def _email(self, send_result=1, side_effect=None):
        patcher = mock.patch.object(alerting, 'EmailMessage')
        email_cls = patcher.start()
        self.addCleanup(patcher.stop)
        email_cls.return_value.send.return_value = send_result
        if side_effect:
            email_cls.return_value.send.side_effect = side_effect
        return email_cls

    # ── recipients ──────────────────────────────────────────────────────────
    def test_groups_and_support_suppression(self):
        self.assertEqual(alerting.recipients('kaz'), KAZ)
        self.assertEqual(alerting.recipients('team'), TEAM)
        with override_settings(KAZ_AND_TEAM='a@x.com, support@autorelister.com.au ,a@x.com'):
            self.assertEqual(alerting.recipients('team'), ['a@x.com'])
        self.assertEqual(alerting.recipients(['support@autorelister.com.au']), [])

    def test_no_recipients_is_a_logged_failure_not_a_send(self):
        email = self._email()
        with override_settings(KAZ_ONLY=['support@autorelister.com.au']):
            self.assertFalse(alerting.send_email('kaz', 's', 'b'))
        email.assert_not_called()

    # ── lifecycle ───────────────────────────────────────────────────────────
    def test_first_then_suppressed_then_resolved(self):
        email = self._email()
        self.assertEqual(alerting.raise_alert('infra:redis', 'team', '[ALERT] Redis down', 'body'), 'sent')
        self.assertEqual(email.call_args.kwargs['to'], TEAM)
        self.assertEqual(email.call_args.kwargs['subject'], '[ALERT] Redis down')
        self.assertIn('First occurrence', email.call_args.kwargs['body'])
        email.return_value.send.assert_called_with(fail_silently=False)
        self.assertEqual(alerting.raise_alert('infra:redis', 'team', '[ALERT] Redis down', 'body'), 'suppressed')
        self.assertEqual(email.call_count, 1)
        self.assertTrue(alerting.resolve_alert('infra:redis', note='PONG again'))
        self.assertEqual(email.call_count, 2)
        self.assertEqual(email.call_args.kwargs['subject'], '[RESOLVED] Redis down')
        self.assertIn('PONG again', email.call_args.kwargs['body'])
        self.assertEqual(alerting.open_alerts(), {})
        self.assertTrue(alerting.resolve_alert('infra:redis'))  # nothing open → no email
        self.assertEqual(email.call_count, 2)

    def test_reminder_only_after_configured_hours(self):
        email = self._email()
        alerting.raise_alert('k', 'kaz', '[ALERT] thing', 'b')
        state = alerting.open_alerts()['k']
        self.assertEqual(alerting.raise_alert('k', 'kaz', '[ALERT] thing', 'b'), 'suppressed')
        # Pretend the first email went out 25 hours ago.
        with alerting._locked_state() as s:
            s['open']['k']['last_sent_at'] = (timezone.now() - timedelta(hours=25)).isoformat()
        self.assertEqual(alerting.raise_alert('k', 'kaz', '[ALERT] thing', 'b'), 'reminded')
        self.assertEqual(email.call_args.kwargs['subject'], '[STILL FAILING] thing')
        self.assertIn('reminder #1', email.call_args.kwargs['body'])
        self.assertEqual(alerting.open_alerts()['k']['first_at'], state['first_at'])
        self.assertEqual(alerting.raise_alert('k', 'kaz', '[ALERT] thing', 'b'), 'suppressed')

    def test_reminders_disabled_with_zero_hours(self):
        self._email()
        with override_settings(ALERT_REMIND_HOURS=0):
            alerting.raise_alert('k', 'kaz', 's', 'b')
            with alerting._locked_state() as s:
                s['open']['k']['last_sent_at'] = (timezone.now() - timedelta(days=30)).isoformat()
            self.assertEqual(alerting.raise_alert('k', 'kaz', 's', 'b'), 'suppressed')

    def test_severity_escalation_sends_immediately(self):
        email = self._email()
        alerting.raise_alert('disk', 'team', '[WARNING] disk 82%', 'b', severity='warning')
        self.assertEqual(alerting.raise_alert('disk', 'team', '[WARNING] disk 85%', 'b', severity='warning'), 'suppressed')
        self.assertEqual(alerting.raise_alert('disk', 'team', '[CRITICAL] disk 91%', 'b', severity='critical'), 'escalated')
        self.assertEqual(email.call_args.kwargs['subject'], '[CRITICAL] disk 91%')
        self.assertIn('warning to critical', email.call_args.kwargs['body'])
        self.assertEqual(alerting.raise_alert('disk', 'team', '[CRITICAL] disk 95%', 'b', severity='critical'), 'suppressed')

    # ── delivery failures ───────────────────────────────────────────────────
    def test_failed_delivery_is_never_recorded(self):
        email = self._email(side_effect=Exception('recipient_suppressed'))
        with self.assertLogs('relister_views', level='ERROR') as logs:
            self.assertEqual(alerting.raise_alert('k', 'kaz', '[ALERT] x', 'b'), 'failed')
        self.assertTrue(any('DELIVERY FAILED' in line and 'recipient_suppressed' in line for line in logs.output))
        self.assertEqual(alerting.open_alerts(), {})
        email.return_value.send.side_effect = None
        self.assertEqual(alerting.raise_alert('k', 'kaz', '[ALERT] x', 'b'), 'sent')

    def test_backend_reporting_zero_sent_is_a_failure(self):
        self._email(send_result=0)
        self.assertFalse(alerting.send_email('kaz', 's', 'b'))

    def test_failed_resolved_email_keeps_issue_open(self):
        email = self._email()
        alerting.raise_alert('k', 'kaz', '[ALERT] x', 'b')
        email.return_value.send.side_effect = Exception('boom')
        self.assertFalse(alerting.resolve_alert('k'))
        self.assertIn('k', alerting.open_alerts())
        email.return_value.send.side_effect = None
        self.assertTrue(alerting.resolve_alert('k'))
        self.assertNotIn('k', alerting.open_alerts())

    # ── weekly test ─────────────────────────────────────────────────────────
    def test_weekly_test_email_goes_to_both_lists(self):
        email = self._email()
        alerting.raise_alert('infra:database', 'team', '[ALERT] DB down', 'b')
        result = alerting.weekly_alert_test_email()
        self.assertEqual(result, {'kaz': True, 'team': True})
        tos = [c.kwargs['to'] for c in email.call_args_list[1:]]
        self.assertEqual(tos, [KAZ, TEAM])
        last = email.call_args.kwargs
        self.assertEqual(last['subject'], '[TEST] AutoRelister alert system is working')
        self.assertIn('Open alerts right now: 1', last['body'])
        self.assertIn('infra:database', last['body'])
