"""RDS backup monitor: latest restorable time of the Sydney DB and Melbourne
copy; > 1 h behind → KAZ_AND_TEAM via the central sender; unreadable → alert;
[RESOLVED] when caught up. Uses the dedicated RDS_MONITOR_* credentials only.

Run: python src/manage.py test team_alerts.tests_rds_monitor
"""
import os
import sys
import tempfile
from datetime import timedelta
from unittest import mock

from botocore.exceptions import ClientError
from django.test import TestCase, override_settings
from django.utils import timezone

from team_alerts import alerting
from team_alerts import rds_monitor as rm

TEAM = ['kazp@citysubaru.com.au', 'dev@example.com']
NOT_FOUND = ClientError({'Error': {'Code': 'DBInstanceNotFound', 'Message': 'x'}}, 'DescribeDBInstances')


class RdsBackupMonitorTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'), ALERT_REMIND_HOURS=24, KAZ_AND_TEAM=TEAM,
            RDS_MONITOR_ACCESS_KEY_ID='AKIAMON', RDS_MONITOR_SECRET_ACCESS_KEY='monsecret',
            RDS_MONITOR_DATABASES=['sydney=ap-southeast-2=relister-prod', 'melbourne=ap-southeast-4=relister-prod'],
            RDS_MONITOR_MAX_LAG_MINUTES=60,
            AWS_VEHICLE_IMAGE_ACCESS_KEY_ID='AKIAS3APP', AWS_VEHICLE_IMAGE_SECRET_ACCESS_KEY='s3secret',
        )
        ctx.enable()
        self.addCleanup(ctx.disable)
        self.boto = mock.Mock()
        self.clients = {}
        self.boto.client.side_effect = lambda service, region_name, **kw: self.clients.setdefault(region_name, mock.Mock())
        mp = mock.patch.dict(sys.modules, {'boto3': self.boto})
        mp.start()
        self.addCleanup(mp.stop)

    def _instance(self, region, minutes_behind):
        c = self.clients.setdefault(region, mock.Mock())
        c.describe_db_instances.return_value = {'DBInstances': [{
            'DBInstanceStatus': 'available', 'LatestRestorableTime': timezone.now() - timedelta(minutes=minutes_behind),
        }]}
        return c

    def _backup_replica(self, region, minutes_behind):
        c = self.clients.setdefault(region, mock.Mock())
        c.describe_db_instances.side_effect = NOT_FOUND
        c.describe_db_instance_automated_backups.return_value = {'DBInstanceAutomatedBackups': [{
            'Status': 'replicating', 'RestoreWindow': {'LatestTime': timezone.now() - timedelta(minutes=minutes_behind)},
        }]}
        return c

    def _run(self):
        with mock.patch.object(alerting, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            result = rm.rds_backup_monitor()
        return result, email_cls

    def test_both_current_sends_nothing_and_uses_monitor_credentials_only(self):
        self._instance('ap-southeast-2', 5)
        self._backup_replica('ap-southeast-4', 20)
        result, email = self._run()
        self.assertEqual(result, {'sydney': 'ok (5m behind)', 'melbourne': 'ok (20m behind)'})
        email.assert_not_called()
        for call in self.boto.client.call_args_list:
            self.assertEqual(call.args[0], 'rds')
            self.assertEqual(call.kwargs['aws_access_key_id'], 'AKIAMON')
            self.assertEqual(call.kwargs['aws_secret_access_key'], 'monsecret')
        self.assertEqual({c.kwargs['region_name'] for c in self.boto.client.call_args_list}, {'ap-southeast-2', 'ap-southeast-4'})

    def test_melbourne_copy_more_than_an_hour_behind_alerts_team_once(self):
        self._instance('ap-southeast-2', 3)
        self._backup_replica('ap-southeast-4', 95)
        result, email = self._run()
        self.assertEqual(result['melbourne'], 'BEHIND 95m')
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], TEAM)
        self.assertEqual(kwargs['subject'], '[ALERT] RDS backups behind for melbourne database — latest restorable time 95 minutes ago')
        self.assertIn('ap-southeast-4', kwargs['body'])
        self.assertIn('automated backup replica', kwargs['body'])
        email.return_value.send.assert_called_once_with(fail_silently=False)
        _, email2 = self._run()  # still behind → no repeat
        email2.assert_not_called()

    def test_exactly_one_hour_is_not_an_alert(self):
        # Freeze the clock: the fixture and the monitor must see the same "now",
        # otherwise the microseconds between them push the lag just past 60 min.
        fixed = timezone.now()
        with mock.patch.object(rm.timezone, 'now', return_value=fixed):
            self._instance('ap-southeast-2', 60)
            self._instance('ap-southeast-4', 1)
            result, email = self._run()
        self.assertEqual(result['sydney'], 'ok (60m behind)')
        email.assert_not_called()

    def test_catching_up_sends_resolved_then_alerts_again(self):
        c = self._instance('ap-southeast-2', 120)
        self._instance('ap-southeast-4', 1)
        self._run()
        c.describe_db_instances.return_value['DBInstances'][0]['LatestRestorableTime'] = timezone.now() - timedelta(minutes=2)
        _, email = self._run()
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED] RDS backups behind for sydney'))
        c.describe_db_instances.return_value['DBInstances'][0]['LatestRestorableTime'] = timezone.now() - timedelta(minutes=200)
        _, email = self._run()
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[ALERT]'))

    def test_unreadable_database_alerts_instead_of_being_ignored(self):
        self._instance('ap-southeast-2', 1)
        c = self.clients.setdefault('ap-southeast-4', mock.Mock())
        c.describe_db_instances.side_effect = ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'no'}}, 'DescribeDBInstances')
        with self.assertLogs('relister_views', level='ERROR'):
            result, email = self._run()
        self.assertTrue(result['melbourne'].startswith('check failed'))
        email.assert_called_once()
        self.assertEqual(email.call_args.kwargs['subject'], '[ALERT] RDS backup check failed for melbourne database')
        self.assertIn('AccessDenied', email.call_args.kwargs['body'])
        c.describe_db_instances.side_effect = None
        c.describe_db_instances.return_value = {'DBInstances': [{'DBInstanceStatus': 'available', 'LatestRestorableTime': timezone.now()}]}
        _, email = self._run()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED] RDS backup check failed'))

    def test_missing_credentials_is_a_check_failure(self):
        self._instance('ap-southeast-2', 1)
        self._instance('ap-southeast-4', 1)
        with override_settings(RDS_MONITOR_ACCESS_KEY_ID=''):
            result, email = self._run()
        self.assertTrue(all(v.startswith('check failed') for v in result.values()))
        self.assertEqual(email.call_count, 2)
        self.assertIn('not configured', email.call_args.kwargs['body'])

    def test_instance_without_backups_is_a_check_failure(self):
        c = self.clients.setdefault('ap-southeast-2', mock.Mock())
        c.describe_db_instances.return_value = {'DBInstances': [{'DBInstanceStatus': 'available'}]}
        self._instance('ap-southeast-4', 1)
        result, email = self._run()
        self.assertIn('no LatestRestorableTime', result['sydney'])
        email.assert_called_once()

    def test_parse_databases(self):
        self.assertEqual(rm.parse_databases('a=r1=id1, b=r2=id2'), [
            {'name': 'a', 'region': 'r1', 'identifier': 'id1'}, {'name': 'b', 'region': 'r2', 'identifier': 'id2'}])
        self.assertEqual(rm.parse_databases(['bad entry', 'x=y=z']), [{'name': 'x', 'region': 'y', 'identifier': 'z'}])
        self.assertEqual(rm.parse_databases(''), [])

    def test_empty_config_is_logged(self):
        with override_settings(RDS_MONITOR_DATABASES=[]), self.assertLogs('relister_views', level='ERROR') as logs:
            result, email = self._run()
        self.assertEqual(result, {})
        self.assertTrue(any('RDS_MONITOR_DATABASES is empty' in line for line in logs.output))
