"""Infrastructure health check: KAZ_AND_TEAM alerted after 2 consecutive
failures via the central sender, once; [RESOLVED] on recovery.

Run: python src/manage.py test team_alerts.tests_health_check
"""
import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from team_alerts import alerting, tasks

TEAM = ['kazp@citysubaru.com.au', 'dev@example.com']


class InfrastructureHealthCheckTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            HEALTHCHECK_STATE_FILE=os.path.join(self.tmp.name, 'state.json'),
            ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'),
            HEALTHCHECK_FAILURES_BEFORE_ALERT=2,
            KAZ_AND_TEAM=TEAM, ALERT_REMIND_HOURS=24,
        )
        ctx.enable()
        self.addCleanup(ctx.disable)

    def _run(self, db_ok=True, redis_ok=True, send_side_effect=None):
        def db():
            if not db_ok:
                raise RuntimeError('db down')

        def rd():
            if not redis_ok:
                raise RuntimeError('redis down')

        with mock.patch.dict(tasks.CHECKS, {'database': db, 'redis': rd}), \
                mock.patch.object(alerting, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            email_cls.return_value.send.side_effect = send_side_effect
            result = tasks.infrastructure_health_check()
        return result, email_cls

    def test_all_ok_sends_nothing(self):
        result, email = self._run()
        self.assertEqual(result, {'database': 'ok'})
        email.assert_not_called()

    def test_redis_is_not_checked_from_celery(self):
        # Redis is Celery's broker — this task cannot run while it is down, so
        # the Redis check lives in the external uptime cron + health endpoint.
        result, email = self._run(redis_ok=False)
        self.assertEqual(result, {'database': 'ok'})
        email.assert_not_called()

    def test_single_failure_does_not_alert(self):
        _, email = self._run(db_ok=False)
        email.assert_not_called()

    def test_second_consecutive_failure_alerts_team_once_then_resolves(self):
        self._run(db_ok=False)
        _, email = self._run(db_ok=False)
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], TEAM)
        self.assertIn('database', kwargs['subject'])
        email.return_value.send.assert_called_once_with(fail_silently=False)
        _, email3 = self._run(db_ok=False)  # still down: no second email
        email3.assert_not_called()
        _, email4 = self._run()  # recovered → one [RESOLVED]
        email4.assert_called_once()
        self.assertTrue(email4.call_args.kwargs['subject'].startswith('[RESOLVED]'))

    def test_recovery_resets_counter(self):
        self._run(db_ok=False)
        self._run()  # recovered (nothing was open → no email)
        _, email = self._run(db_ok=False)  # one failure again — not two in a row
        email.assert_not_called()

    def test_failed_delivery_is_retried_next_run(self):
        self._run(db_ok=False)
        _, email = self._run(db_ok=False, send_side_effect=Exception('flashpost 500'))  # 2nd failure, delivery fails
        self.assertEqual(alerting.open_alerts(), {})
        _, email = self._run(db_ok=False)  # 3rd failure — alert retried
        email.assert_called_once()


class InfrastructureHealthEndpointTests(TestCase):
    """GET /api/team-alerts/health/ — polled by monitoring/uptime_check.py. Runs
    the checks in the web process (no Celery, no Redis needed to run)."""

    def _get(self, query, redis_ok=True, db_ok=True):
        def db():
            if not db_ok:
                raise RuntimeError('db down')

        def rd():
            if not redis_ok:
                raise ConnectionError('Error 111 connecting to redis:6379')
        with mock.patch.dict(tasks.CHECKS, {'database': db, 'redis': rd}):
            return self.client.get(reverse('team_alerts:infra_health') + query)

    def test_redis_ok_is_200(self):
        resp = self._get('?component=redis')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['redis'], 'ok')

    def test_redis_down_is_503_with_the_error(self):
        resp = self._get('?component=redis', redis_ok=False)
        self.assertEqual(resp.status_code, 503)
        self.assertTrue(resp.json()['redis'].startswith('down: ConnectionError'))

    def test_no_component_checks_everything_and_needs_no_auth(self):
        resp = self._get('', db_ok=False)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()['redis'], 'ok')
        self.assertTrue(resp.json()['database'].startswith('down:'))

    def test_unknown_component_is_400(self):
        self.assertEqual(self._get('?component=kafka').status_code, 400)
