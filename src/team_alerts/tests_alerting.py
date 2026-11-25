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

    # ── SES fallback + Flashpost self-monitoring ────────────────────────────
    def _ses(self, message_id='abc'):
        patcher = mock.patch.object(alerting, 'boto3', create=True)
        boto = patcher.start()
        self.addCleanup(patcher.stop)
        boto.client.return_value.send_email.return_value = {'MessageId': message_id}
        # alerting imports boto3 lazily inside _ses_send; make that import hit our mock.
        import_patcher = mock.patch.dict('sys.modules', {'boto3': boto})
        import_patcher.start()
        self.addCleanup(import_patcher.stop)
        return boto

    @override_settings(ALERT_FALLBACK_SES_REGION='ap-southeast-2', ALERT_FALLBACK_SES_ACCESS_KEY_ID='k',
                       ALERT_FALLBACK_SES_SECRET_ACCESS_KEY='s', ALERT_FALLBACK_SES_FROM_EMAIL='alerts@example.com')
    def test_flashpost_error_is_reported_through_ses_not_flashpost(self):
        flashpost = self._email()
        boto = self._ses()
        alerting._flashpost_errored = False
        result = alerting.report_flashpost_error(403, '{"error":"sending suspended"}', subject='Welcome', to=['dealer@x.com'])
        self.assertEqual(result, 'sent')
        flashpost.assert_not_called()
        boto.client.assert_called_once_with('ses', region_name='ap-southeast-2', aws_access_key_id='k', aws_secret_access_key='s')
        call = boto.client.return_value.send_email.call_args.kwargs
        self.assertEqual(call['Source'], 'alerts@example.com')
        self.assertEqual(call['Destination'], {'ToAddresses': TEAM})
        self.assertEqual(call['Message']['Subject']['Data'], '[ALERT] Flashpost email error 403: sending suspended')
        self.assertIn('dealer@x.com', call['Message']['Body']['Text']['Data'])
        # Repeat while still failing → suppressed; 422 is a separate issue.
        self.assertEqual(alerting.report_flashpost_error(403, 'x', subject='Invoice', to=['a@x.com']), 'suppressed')
        self.assertEqual(alerting.report_flashpost_error(422, '{"error":"recipient_suppressed"}', subject='Invoice', to=['a@x.com']), 'sent')
        self.assertEqual(boto.client.return_value.send_email.call_count, 2)
        self.assertEqual(alerting.open_alerts()['flashpost:422']['transport'], 'ses')
        # Flashpost accepts a message again → both resolved through SES.
        alerting.report_flashpost_ok()
        self.assertEqual(alerting.open_alerts(), {})
        self.assertEqual(boto.client.return_value.send_email.call_count, 4)
        self.assertTrue(boto.client.return_value.send_email.call_args.kwargs['Message']['Subject']['Data'].startswith('[RESOLVED] Flashpost email error'))
        flashpost.assert_not_called()

    def test_ses_not_configured_is_logged_not_raised(self):
        boto = self._ses()
        with override_settings(ALERT_FALLBACK_SES_REGION=''), self.assertLogs('relister_views', level='ERROR') as logs:
            self.assertEqual(alerting.report_flashpost_error(403, 'x', subject='s', to=['a@x.com']), 'failed')
        self.assertTrue(any('SES fallback not configured' in line for line in logs.output))
        boto.client.assert_not_called()
        self.assertEqual(alerting.open_alerts(), {})

    def test_flashpost_failure_while_sending_an_alert_does_not_deadlock(self):
        # Flashpost rejects the alert → the backend hook reports it via SES from
        # INSIDE send_email. Simulated by a side effect that calls report_flashpost_error.
        email = self._email()
        boto = self._ses()
        alerting._flashpost_errored = False
        with override_settings(ALERT_FALLBACK_SES_REGION='r', ALERT_FALLBACK_SES_ACCESS_KEY_ID='k', ALERT_FALLBACK_SES_SECRET_ACCESS_KEY='s'):
            def reject(**kwargs):
                alerting.report_flashpost_error(403, 'suspended', subject='x', to=TEAM)
                raise Exception('HTTP 403')
            email.return_value.send.side_effect = reject
            self.assertEqual(alerting.raise_alert('infra:redis', 'team', '[ALERT] Redis down', 'b'), 'failed')
        self.assertEqual(list(alerting.open_alerts()), ['flashpost:403'])
        boto.client.return_value.send_email.assert_called_once()

    # ── S3 upload monitoring ────────────────────────────────────────────────
    def test_s3_upload_failure_alerts_team_once_and_resolves_on_success(self):
        from botocore.exceptions import ClientError
        email = self._email()
        alerting._s3_errored = False
        exc = ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'denied'}}, 'PutObject')
        self.assertEqual(alerting.report_s3_upload_failure('vehicles/ab/abc/large.webp', exc, bucket='images'), 'sent')
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], TEAM)
        self.assertEqual(kwargs['subject'], '[ALERT] S3 image upload failing — AccessDenied')
        self.assertIn('Bucket: images', kwargs['body'])
        self.assertEqual(alerting.report_s3_upload_failure('k2', exc, bucket='images'), 'suppressed')
        alerting.report_s3_upload_ok()
        self.assertEqual(email.call_count, 2)
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED] S3 image upload failing'))
        alerting.report_s3_upload_ok()  # nothing open → nothing
        self.assertEqual(email.call_count, 2)

    def test_image_pipeline_reports_put_object_failure_and_reraises(self):
        from botocore.exceptions import ClientError
        from VehicleListing import image_pipeline
        exc = ClientError({'Error': {'Code': 'NoSuchBucket', 'Message': 'x'}}, 'PutObject')
        client = mock.Mock()
        client.put_object.side_effect = exc
        with mock.patch.object(image_pipeline, '_s3_client', return_value=client), \
                mock.patch.object(alerting, 'report_s3_upload_failure') as report, \
                override_settings(AWS_VEHICLE_IMAGE_BUCKET='images'):
            with self.assertRaises(ClientError):
                image_pipeline.upload_variant('some/key.webp', b'bytes')
        report.assert_called_once_with('some/key.webp', exc, bucket='images')
        client.put_object.side_effect = None
        with mock.patch.object(image_pipeline, '_s3_client', return_value=client), \
                mock.patch.object(alerting, 'report_s3_upload_ok') as ok, \
                override_settings(AWS_VEHICLE_IMAGE_BUCKET='images'):
            image_pipeline.upload_variant('some/key.webp', b'bytes')
        ok.assert_called_once()

    def test_email_backend_reports_http_errors_and_recovery(self):
        import requests
        from relister.email_backend import FlashpostEmailBackend
        from django.core.mail import EmailMessage as DjangoEmail
        backend = FlashpostEmailBackend(fail_silently=True)
        with override_settings(FLASHPOST_API_URL='https://fp.example', FLASHPOST_API_KEY='k'):
            backend.api_url, backend.api_key = 'https://fp.example', 'k'
        msg = DjangoEmail(subject='Welcome', body='hi', from_email='hello@x.com', to=['dealer@x.com'])
        resp = mock.Mock(status_code=422, text='{"error":"recipient_suppressed"}')
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
        backend.open()
        with mock.patch.object(backend._session, 'post', return_value=resp), \
                mock.patch.object(alerting, 'report_flashpost_error') as report:
            self.assertEqual(backend.send_messages([msg]), 0)
        report.assert_called_once_with(422, '{"error":"recipient_suppressed"}', subject='Welcome', to=['dealer@x.com'])
        ok_resp = mock.Mock(status_code=200, text='{}')
        with mock.patch.object(backend._session, 'post', return_value=ok_resp), \
                mock.patch.object(alerting, 'report_flashpost_ok') as ok:
            self.assertEqual(backend.send_messages([msg]), 1)
        ok.assert_called_once()
        backend.close()

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
