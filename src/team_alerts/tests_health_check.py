"""Infrastructure health check: alert to Kaz after 2 consecutive failures, once.

Run: python src/manage.py test team_alerts.tests_health_check
"""
import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from team_alerts import tasks


class InfrastructureHealthCheckTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmp.name, 'state.json')
        self.settings_ctx = override_settings(
            HEALTHCHECK_STATE_FILE=self.state_file,
            HEALTHCHECK_FAILURES_BEFORE_ALERT=2,
            HEALTHCHECK_ALERT_EMAIL='kazp@citysubaru.com.au',
        )
        self.settings_ctx.enable()
        self.addCleanup(self.settings_ctx.disable)
        self.addCleanup(self.tmp.cleanup)

    def _run(self, db_ok=True, redis_ok=True):
        def db():
            if not db_ok:
                raise RuntimeError('db down')

        def rd():
            if not redis_ok:
                raise RuntimeError('redis down')

        with mock.patch.dict(tasks.CHECKS, {'database': db, 'redis': rd}), \
                mock.patch.object(tasks, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            result = tasks.infrastructure_health_check()
        return result, email_cls

    def test_all_ok_sends_nothing(self):
        result, email = self._run()
        self.assertEqual(result, {'database': 'ok', 'redis': 'ok'})
        email.assert_not_called()

    def test_single_failure_does_not_alert(self):
        _, email = self._run(redis_ok=False)
        email.assert_not_called()

    def test_second_consecutive_failure_alerts_kaz_once(self):
        self._run(redis_ok=False)
        _, email = self._run(redis_ok=False)
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], ['kazp@citysubaru.com.au'])
        self.assertIn('redis', kwargs['subject'])
        email.return_value.send.assert_called_once_with(fail_silently=False)
        # Third failure: still down, no second email.
        _, email3 = self._run(redis_ok=False)
        email3.assert_not_called()

    def test_recovery_resets_counter(self):
        self._run(db_ok=False)
        self._run()  # recovered
        _, email = self._run(db_ok=False)  # one failure again — not two in a row
        email.assert_not_called()

    def test_failed_delivery_is_retried_next_run(self):
        self._run(db_ok=False)
        with mock.patch.dict(tasks.CHECKS, {'database': lambda: (_ for _ in ()).throw(RuntimeError('x')), 'redis': lambda: None}), \
                mock.patch.object(tasks, 'EmailMessage') as email_cls:
            email_cls.return_value.send.side_effect = Exception('flashpost 500')
            tasks.infrastructure_health_check()  # 2nd failure, delivery fails
        _, email = self._run(db_ok=False)  # 3rd failure — alert retried
        email.assert_called_once()
