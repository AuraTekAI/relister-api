"""ZenRows credit monitoring: daily→3-hourly cadence, 80%/95% alerts with
escalation, run-out projection, immediate 402 alert — all via the central
sender to KAZ_AND_TEAM, with [RESOLVED] when the condition clears.

Run: python src/manage.py test team_alerts.tests_zenrows_monitor
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from team_alerts import alerting
from team_alerts import zenrows_monitor as zm

TEAM = ['kazp@citysubaru.com.au', 'team@example.com']

# Fixed per test-module "now" so repeated calls with the same start_days_ago
# describe the SAME billing period.
NOW = timezone.now().replace(microsecond=0)


def _usage(percent, start_days_ago=10, period_days=30, plan='Business'):
    start = NOW - timedelta(days=start_days_ago)
    return {
        'percent': float(percent),
        'period_start': start,
        'period_end': start + timedelta(days=period_days),
        'plan': plan,
        'status': 'ACTIVE',
        'raw': {},
    }


def _subjects(email_cls):
    return [c.kwargs['subject'] for c in email_cls.call_args_list]


class ZenRowsUsageMonitorTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            ZENROWS_MONITOR_STATE_FILE=os.path.join(self.tmp.name, 'state.json'),
            ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'),
            ALERT_REMIND_HOURS=24,
            ZENROWS_MONITOR_INTERVAL_HOURS=24,
            ZENROWS_MONITOR_ELEVATED_INTERVAL_HOURS=3,
            ZENROWS_MONITOR_ELEVATED_FROM_PCT=80,
            ZENROWS_MONITOR_WARN_PCT=80,
            ZENROWS_MONITOR_CRIT_PCT=95,
            ZENROWS_MONITOR_PROJECTION_MIN_ELAPSED_PCT=10,
            KAZ_AND_TEAM=TEAM,
        )
        ctx.enable()
        self.addCleanup(ctx.disable)

    def _run(self, usage, force=False, send_side_effect=None):
        with mock.patch.object(zm, 'fetch_zenrows_usage', return_value=usage), \
                mock.patch.object(alerting, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            email_cls.return_value.send.side_effect = send_side_effect
            result = zm.zenrows_usage_monitor(force=force)
        return result, email_cls

    # ── cadence ─────────────────────────────────────────────────────────────
    def test_low_usage_checks_once_a_day(self):
        result, _ = self._run(_usage(30))
        self.assertTrue(result['checked'])
        result, _ = self._run(_usage(30))  # 3h later Beat tick → skipped, no API call
        self.assertEqual(result, {'checked': False, 'reason': 'not_due'})

    def test_high_usage_checks_every_run(self):
        self._run(_usage(85, start_days_ago=28))
        result, _ = self._run(_usage(86, start_days_ago=28))
        self.assertTrue(result['checked'])

    def test_low_usage_checks_again_after_a_day(self):
        self._run(_usage(30))
        state = zm._load_state()
        state['last_checked_at'] = (timezone.now() - timedelta(hours=24)).isoformat()
        zm._save_state(state)
        result, _ = self._run(_usage(31))
        self.assertTrue(result['checked'])

    # ── thresholds ──────────────────────────────────────────────────────────
    def test_below_80_sends_nothing(self):
        _, email = self._run(_usage(50))
        email.assert_not_called()

    def test_80_percent_warns_team_once(self):
        _, email = self._run(_usage(82, start_days_ago=25))
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], TEAM)
        self.assertTrue(kwargs['subject'].startswith('[WARNING] ZenRows credits at 82%'))
        email.return_value.send.assert_called_once_with(fail_silently=False)
        _, email2 = self._run(_usage(84, start_days_ago=25))  # still warning → no repeat
        email2.assert_not_called()

    def test_95_percent_escalates_to_critical_once(self):
        self._run(_usage(82, start_days_ago=25))
        _, email = self._run(_usage(96, start_days_ago=25))
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[CRITICAL] ZenRows credits at 96%'))
        _, email2 = self._run(_usage(98, start_days_ago=25))
        email2.assert_not_called()

    def test_new_billing_period_resolves_then_alerts_again(self):
        self._run(_usage(96, start_days_ago=29))
        _, email = self._run(_usage(5, start_days_ago=1))  # renewed → [RESOLVED]
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED] ZenRows credits at 96%'))
        _, email = self._run(_usage(81, start_days_ago=25), force=True)  # new period reaches 80 → warns again
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[WARNING]'))

    # ── projection ──────────────────────────────────────────────────────────
    def test_projected_runout_before_renewal_alerts_once(self):
        # 10 of 30 days elapsed, 50% used → 150% by renewal → runs out on day 20.
        _, email = self._run(_usage(50, start_days_ago=10))
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], TEAM)
        self.assertIn('projected to run out', kwargs['subject'])
        self.assertIn('Projected usage by renewal at the current rate: 150%', kwargs['body'])
        _, email2 = self._run(_usage(52, start_days_ago=10), force=True)
        email2.assert_not_called()
        _, email3 = self._run(_usage(30, start_days_ago=10), force=True)  # back on track → resolved
        email3.assert_called_once()
        self.assertTrue(email3.call_args.kwargs['subject'].startswith('[RESOLVED]'))

    def test_on_track_usage_does_not_project(self):
        _, email = self._run(_usage(30, start_days_ago=10))  # → 90% by renewal
        email.assert_not_called()

    def test_projection_ignored_at_start_of_period(self):
        _, email = self._run(_usage(5, start_days_ago=0, period_days=30))  # <10% elapsed
        email.assert_not_called()

    def test_project_runout_math(self):
        now = datetime(2026, 1, 11, tzinfo=dt_timezone.utc)
        start, end = datetime(2026, 1, 1, tzinfo=dt_timezone.utc), datetime(2026, 1, 31, tzinfo=dt_timezone.utc)
        will, projected, runout = zm.project_runout(50.0, start, end, now)
        self.assertTrue(will)
        self.assertEqual(projected, 150.0)
        self.assertEqual(runout, datetime(2026, 1, 21, tzinfo=dt_timezone.utc))

    # ── failures ────────────────────────────────────────────────────────────
    def test_failed_send_is_retried_next_run(self):
        self._run(_usage(85, start_days_ago=28), send_side_effect=Exception('flashpost 500'))
        _, email = self._run(_usage(85, start_days_ago=28))
        email.assert_called_once()

    def test_usage_api_error_is_logged_not_raised(self):
        with mock.patch.object(zm, 'fetch_zenrows_usage', side_effect=RuntimeError('usage endpoint HTTP 500')), \
                mock.patch.object(alerting, 'EmailMessage') as email_cls:
            result = zm.zenrows_usage_monitor()
        self.assertFalse(result['checked'])
        email_cls.assert_not_called()

    def test_fetch_parses_subscription_response(self):
        payload = {
            'status': 'ACTIVE', 'period_starts_at': '2026-11-02T00:00:00Z', 'period_ends_at': '2026-12-02T00:00:00Z',
            'usage': 103.8, 'usage_percent': 34, 'plan': {'name': 'Business'}, 'top_ups': [],
        }
        fake = mock.Mock(status_code=200, json=mock.Mock(return_value=payload))
        with override_settings(ZENROWS_API_KEY='test-key'), mock.patch.object(zm.requests, 'get', return_value=fake) as get:
            usage = zm.fetch_zenrows_usage()
        self.assertEqual(usage['percent'], 34.0)
        self.assertEqual(usage['plan'], 'Business')
        self.assertEqual(usage['period_end'].isoformat(), '2026-12-02T00:00:00+00:00')
        self.assertEqual(get.call_args.kwargs['headers'], {'X-API-Key': 'test-key'})


class ZenRows402HookTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            ZENROWS_MONITOR_STATE_FILE=os.path.join(self.tmp.name, 'state.json'),
            ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'),
            ALERT_REMIND_HOURS=24,
            KAZ_AND_TEAM=TEAM,
        )
        ctx.enable()
        self.addCleanup(ctx.disable)
        zm._saw_402 = False

    def _report(self, status, url='https://www.gumtree.com.au/s-cars/x'):
        resp = mock.Mock(status_code=status, text='{"code":"PAYMENT_REQUIRED"}')
        with mock.patch.object(alerting, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            sent = zm.report_zenrows_response(resp, url)
        return sent, email_cls

    def test_402_alerts_immediately(self):
        sent, email = self._report(402)
        self.assertTrue(sent)
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], TEAM)
        self.assertIn('402', kwargs['subject'])
        self.assertIn('gumtree.com.au', kwargs['body'])
        email.return_value.send.assert_called_once_with(fail_silently=False)

    def test_repeat_402_is_suppressed_and_next_success_resolves(self):
        self._report(402)
        sent, email = self._report(402)
        self.assertFalse(sent)
        email.assert_not_called()
        sent, email = self._report(200)
        self.assertFalse(sent)
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED] ZenRows 402'))
        _, email = self._report(200)  # already resolved → nothing
        email.assert_not_called()

    def test_non_402_does_nothing_when_nothing_open(self):
        for status in (200, 404, 422, 500):
            sent, email = self._report(status)
            self.assertFalse(sent)
            email.assert_not_called()

    def test_hook_wraps_client_get_and_returns_response_untouched(self):
        import zenrows
        zm.install_zenrows_402_hook()
        self.assertTrue(getattr(zenrows.ZenRowsClient.get, '_relister_402_hook', False))
        fake = mock.Mock(status_code=402, text='out of credits')
        with mock.patch.object(zm.requests, 'get', return_value=fake), \
                mock.patch.object(zm, 'report_zenrows_response') as report:
            resp = zenrows.ZenRowsClient('k').get('https://example.com/page', params={'js_render': 'true'})
        self.assertIs(resp, fake)
        report.assert_called_once_with(fake, 'https://example.com/page')
        zm.install_zenrows_402_hook()  # idempotent — no double wrap
        self.assertFalse(getattr(zenrows.ZenRowsClient.get._relister_original, '_relister_402_hook', False))
