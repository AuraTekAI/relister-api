"""Scheduled-task monitor: overdue detection from each task's own crontab + grace,
alert to KAZ_AND_TEAM via the central sender, [RESOLVED] after the task runs again.

Run: python src/manage.py test team_alerts.tests_schedule_monitor
"""
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from team_alerts import alerting
from team_alerts import schedule_monitor as sm

TEAM = ['kazp@citysubaru.com.au', 'team@example.com']


class ScheduledTaskMonitorTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'),
            ALERT_REMIND_HOURS=24,
            SCHEDULE_MONITOR_GRACE_MINUTES=120,
            SCHEDULE_MONITOR_TASK_NAMES=['Check_Trial_Expiry', 'Clean_30_days_Old_logs'],
            KAZ_AND_TEAM=TEAM,
        )
        ctx.enable()
        self.addCleanup(ctx.disable)
        # The post_migrate receiver in VehicleListing/apps.py creates the real Beat
        # rows while the test DB is built; clear them so these fixtures own the names.
        PeriodicTask.objects.all().delete()
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

    def _run(self, send_side_effect=None):
        with mock.patch.object(alerting, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            email_cls.return_value.send.side_effect = send_side_effect
            result = sm.scheduled_task_monitor()
        return result, email_cls

    def test_recent_runs_send_nothing(self):
        self._set_last_run(self.trial, timedelta(hours=3))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        result, email = self._run()
        self.assertEqual(result, {'Check_Trial_Expiry': 'ok', 'Clean_30_days_Old_logs': 'ok'})
        email.assert_not_called()

    def test_daily_task_that_ran_on_time_14h_ago_is_not_overdue(self):
        # Regression: due used to be anchor + remaining_estimate(anchor), which
        # flagged a daily task overdue ~13 h after it ran exactly on schedule.
        now = timezone.now()
        ran = (now - timedelta(hours=14)).replace(second=30, microsecond=0)
        local = ran.astimezone(self.trial.crontab.timezone)
        cron = CrontabSchedule.objects.create(minute=str(local.minute), hour=str(local.hour),
                                              timezone=self.trial.crontab.timezone)
        PeriodicTask.objects.filter(pk=self.trial.pk).update(crontab=cron, last_run_at=ran)
        self._set_last_run(self.cleanup, timedelta(hours=3))
        self.trial.refresh_from_db()
        due, anchor, _ = sm.expected_due_time(self.trial, now)
        self.assertEqual(anchor, ran)
        self.assertGreater(due, now)                                  # next run is tomorrow
        self.assertLess(due - ran, timedelta(hours=24, minutes=1))    # ...exactly one period later
        result, email = self._run()
        self.assertEqual(result['Check_Trial_Expiry'], 'ok')
        email.assert_not_called()

    def test_expected_due_time_is_now_plus_remaining(self):
        now = timezone.now()
        pt = mock.Mock(last_run_at=now - timedelta(hours=14), date_changed=None)
        pt.schedule.remaining_estimate.return_value = timedelta(hours=10)
        due, anchor, label = sm.expected_due_time(pt, now)
        pt.schedule.remaining_estimate.assert_called_once_with(pt.last_run_at)
        self.assertEqual((due, anchor, label), (now + timedelta(hours=10), pt.last_run_at, 'last run'))
        # A run that was missed: Celery reports a negative remaining → due in the past.
        pt.schedule.remaining_estimate.return_value = timedelta(hours=-5)
        self.assertEqual(sm.expected_due_time(pt, now)[0], now - timedelta(hours=5))

    def test_task_missed_by_more_than_grace_alerts_team_once(self):
        # Last ran 2 days ago → the next daily run was due ~1 day ago, well past 2h grace.
        self._set_last_run(self.trial, timedelta(days=2))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        result, email = self._run()
        self.assertTrue(result['Check_Trial_Expiry'].startswith('overdue'))
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], TEAM)
        self.assertIn('Check_Trial_Expiry', kwargs['subject'])
        self.assertNotIn('Clean_30_days_Old_logs', kwargs['subject'])
        email.return_value.send.assert_called_once_with(fail_silently=False)
        _, email2 = self._run()  # still overdue → no repeat
        email2.assert_not_called()

    def test_within_grace_is_not_overdue(self):
        due_anchor = timezone.now() - timedelta(hours=1)
        with mock.patch.object(sm, 'expected_due_time', return_value=(due_anchor, due_anchor - timedelta(days=1), 'last run')):
            self._set_last_run(self.trial, timedelta(days=1, hours=1))
            self._set_last_run(self.cleanup, timedelta(hours=3))
            _, email = self._run()
        email.assert_not_called()

    def test_recovery_resolves_and_overdue_again_alerts_again(self):
        self._set_last_run(self.trial, timedelta(days=2))
        self._set_last_run(self.cleanup, timedelta(hours=3))
        self._run()  # alert 1
        self._set_last_run(self.trial, timedelta(hours=2))  # task ran again → recovered
        _, email = self._run()
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED]'))
        self._set_last_run(self.trial, timedelta(days=3))  # overdue again
        _, email = self._run()
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[ALERT]'))

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
        self._run(send_side_effect=Exception('flashpost 500'))
        _, email = self._run()
        email.assert_called_once()
