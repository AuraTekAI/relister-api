"""Scheduled-task monitor: overdue detection from each task's own crontab + grace,
one alert per outage to KAZ_AND_TEAM, re-armed after the task runs again.

Run: python src/manage.py test team_alerts.tests_schedule_monitor
"""
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from team_alerts import schedule_monitor as sm


class ScheduledTaskMonitorTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            SCHEDULE_MONITOR_STATE_FILE=os.path.join(self.tmp.name, 'state.json'),
            SCHEDULE_MONITOR_GRACE_MINUTES=120,
            SCHEDULE_MONITOR_TASK_NAMES=['Check_Trial_Expiry', 'Clean_30_days_Old_logs'],
            KAZ_AND_TEAM=['kazp@citysubaru.com.au', 'team@example.com'],
        )
        ctx.enable()
        self.addCleanup(ctx.disable)
        daily = CrontabSchedule.objects.create(minute='5', hour='0')
        self.trial = PeriodicTask.objects.create(
            name='Check_Trial_Expiry', task='VehicleListing.tasks.check_trial_expiry_task', crontab=daily, enabled=True,
        )
        self.cleanup = PeriodicTask.objects.create(
            name='Clean_30_days_Old_logs', task='VehicleListing.tasks.cleanup_old_logs',
            crontab=CrontabSchedule.objects.create(minute='0', hour='1'), enabled=True,
        )

    def _set_last_run(self, pt, ago):
        PeriodicTask.objects.filter(pk=pt.pk).update(last_run_at=timezone.now() - ago)

    def _run(self):
        with mock.patch.object(sm, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            result = sm.scheduled_task_monitor()
        return result, email_cls

    def test_recent_runs_send_nothing(self):
        self._set_last_run(self.trial, timedelta(hours=3))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        result, email = self._run()
        self.assertEqual(result, {'Check_Trial_Expiry': 'ok', 'Clean_30_days_Old_logs': 'ok'})
        email.assert_not_called()

    def test_task_missed_by_more_than_grace_alerts_kaz_and_team_once(self):
        # Last ran 2 days ago → the next daily run was due ~1 day ago, well past 2h grace.
        self._set_last_run(self.trial, timedelta(days=2))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        result, email = self._run()
        self.assertTrue(result['Check_Trial_Expiry'].startswith('overdue'))
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], ['kazp@citysubaru.com.au', 'team@example.com'])
        self.assertIn('Check_Trial_Expiry', kwargs['subject'])
        self.assertNotIn('Clean_30_days_Old_logs', kwargs['subject'])
        email.return_value.send.assert_called_once_with(fail_silently=False)
        # Still overdue on the next check → no repeat email.
        _, email2 = self._run()
        email2.assert_not_called()

    def test_within_grace_is_not_overdue(self):
        # Due time passed 1 hour ago, grace is 2 hours → still ok.
        due_anchor = timezone.now() - timedelta(hours=1)
        with mock.patch.object(sm, 'expected_due_time', return_value=(due_anchor, due_anchor - timedelta(days=1), 'last run')):
            self._set_last_run(self.trial, timedelta(days=1, hours=1))
            self._set_last_run(self.cleanup, timedelta(hours=3))
            _, email = self._run()
        email.assert_not_called()

    def test_recovery_then_overdue_again_alerts_again(self):
        self._set_last_run(self.trial, timedelta(days=2))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        self._run()  # alert 1
        self._set_last_run(self.trial, timedelta(hours=2))  # task ran again → recovered
        _, email = self._run()
        email.assert_not_called()
        self._set_last_run(self.trial, timedelta(days=3))  # overdue again
        _, email = self._run()
        email.assert_called_once()

    def test_never_run_task_measured_from_date_changed(self):
        PeriodicTask.objects.filter(pk=self.trial.pk).update(last_run_at=None, date_changed=timezone.now() - timedelta(days=2))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        result, email = self._run()
        self.assertTrue(result['Check_Trial_Expiry'].startswith('overdue'))
        email.assert_called_once()
        self.assertIn('Enabled/changed', email.call_args.kwargs['body'])

    def test_disabled_task_is_ignored(self):
        PeriodicTask.objects.filter(pk=self.trial.pk).update(enabled=False, last_run_at=timezone.now() - timedelta(days=10))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        result, email = self._run()
        self.assertEqual(result['Check_Trial_Expiry'], 'disabled')
        email.assert_not_called()

    def test_failed_send_is_retried_next_run(self):
        self._set_last_run(self.trial, timedelta(days=2))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        with mock.patch.object(sm, 'EmailMessage') as email_cls:
            email_cls.return_value.send.side_effect = Exception('flashpost 500')
            sm.scheduled_task_monitor()
        _, email = self._run()
        email.assert_called_once()
