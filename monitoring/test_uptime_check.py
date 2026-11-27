"""Standard-library tests for the external uptime monitor (no Django needed).

Run:  python3 -m unittest monitoring/test_uptime_check.py   (from relister-api/)
"""
import os
import tempfile
import unittest
from unittest import mock

import uptime_check as uc


def conf(tmpdir, **over):
    base = {
        'targets': [
            ('website', 'https://site.example/', lambda s: s == 200),
            ('api', 'https://api.example/api/login/', lambda s: s < 500),
        ],
        'timeout': 5.0, 'failures_before_alert': 2,
        'state_file': os.path.join(tmpdir, 'state.json'),
        'recipients': ['kazp@citysubaru.com.au', 'team@example.com'],
        'from_email': 'hello@autorelister.com.au', 'flashpost_url': 'https://fp.example', 'flashpost_key': 'k',
        'notify_recovery': True,
    }
    base.update(over)
    return base


class UptimeCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = conf(self.tmp.name)
        self.sent = []

    def _send(self, _conf, subject, body, transport='flashpost'):
        self.sent.append((subject, body))
        self.transports = getattr(self, 'transports', []) + [transport]
        return True

    def _run(self, website=200, api=405, api_error=None, redis=200):
        def check(url, timeout):
            if 'site' in url:
                return (website, 0.1, None) if website else (None, timeout, 'timed out')
            if 'health' in url:
                return (redis, 0.1, None) if redis else (None, timeout, 'timed out')
            return (api, 0.1, None) if api else (None, timeout, api_error or 'connection refused')
        return uc.run(self.conf, check=check, send=self._send)

    def test_healthy_sends_nothing(self):
        r = self._run()
        self.assertEqual(r, {'website': 'up', 'api': 'up'})
        self.assertEqual(self.sent, [])

    def test_api_405_counts_as_up_and_website_404_as_down(self):
        r = self._run(website=404, api=405)
        self.assertEqual(r['api'], 'up')
        self.assertEqual(r['website'], 'down (1)')

    def test_alert_after_two_consecutive_failures_once(self):
        self._run(api=None)
        self.assertEqual(self.sent, [])  # 1 failure: not yet
        self._run(api=None)
        self.assertEqual(len(self.sent), 1)
        subject, body = self.sent[0]
        self.assertTrue(subject.startswith('[ALERT] Relister api is DOWN'))
        self.assertIn('https://api.example/api/login/', body)
        self._run(api=503)
        self.assertEqual(len(self.sent), 1)  # still down: no repeat

    def test_one_failure_then_recovery_resets_counter(self):
        self._run(website=None)
        self._run()
        self._run(website=None)
        self.assertEqual(self.sent, [])

    def test_recovery_notice_then_new_outage_alerts_again(self):
        self._run(website=None)
        self._run(website=None)
        self._run()
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1][0].startswith('[RECOVERED] Relister website'))
        self._run(website=500)
        self._run(website=500)
        self.assertEqual(len(self.sent), 3)

    def test_failed_send_is_retried_next_run(self):
        calls = []

        def flaky(_c, subject, body, transport='flashpost'):
            calls.append(subject)
            return len(calls) > 1

        def check(url, timeout):
            return (None, timeout, 'x')
        uc.run(self.conf, check=check, send=flaky)
        uc.run(self.conf, check=check, send=flaky)  # 2nd failure → send fails
        uc.run(self.conf, check=check, send=flaky)  # retried → ok
        uc.run(self.conf, check=check, send=flaky)  # alerted → silent
        self.assertEqual(len([c for c in calls if 'website' in c]), 2)

    def test_flashpost_payload_and_recipients(self):
        captured = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            captured['url'] = req.full_url
            captured['auth'] = req.get_header('Authorization')
            captured['body'] = req.data
            return Resp()
        with mock.patch.object(uc.urllib.request, 'urlopen', fake_urlopen):
            ok = uc.send_email(self.conf, 'subj', 'body')
        self.assertTrue(ok)
        self.assertEqual(captured['url'], 'https://fp.example/v1/send')
        self.assertEqual(captured['auth'], 'Bearer k')
        self.assertIn(b'"to": ["kazp@citysubaru.com.au", "team@example.com"]', captured['body'])

    def test_missing_flashpost_config_does_not_crash(self):
        c = conf(self.tmp.name, flashpost_url='', flashpost_key='')
        self.assertFalse(uc.send_email(c, 's', 'b'))

    # ── redis via the health endpoint, alerted through SES ──────────────────
    def test_redis_503_twice_alerts_once_via_ses_and_recovers(self):
        self.conf['targets'].append(
            ('redis', 'https://api.example/api/team-alerts/health/?component=redis', lambda s: s == 200, 'ses'))
        r = self._run(redis=503)
        self.assertEqual(r['redis'], 'down (1)')
        self.assertEqual(self.sent, [])
        self._run(redis=503)
        self.assertEqual(len(self.sent), 1)
        subject, body = self.sent[0]
        self.assertTrue(subject.startswith('[ALERT] Relister redis is DOWN — HTTP 503'))
        self.assertIn("Redis is Celery's broker", body)
        self.assertEqual(self.transports, ['ses'])
        self._run(redis=None)  # still down (endpoint unreachable) → no repeat
        self.assertEqual(len(self.sent), 1)
        self._run(redis=200)
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1][0].startswith('[RECOVERED] Relister redis'))
        self.assertEqual(self.transports, ['ses', 'ses'])
        # website/api keep going through Flashpost
        self._run(website=None); self._run(website=None)
        self.assertEqual(self.transports[-1], 'flashpost')

    def test_ses_send_signs_request_and_never_calls_flashpost(self):
        c = conf(self.tmp.name, ses_region='ap-southeast-2', ses_access_key='AKIAEXAMPLE',
                 ses_secret_key='secret', ses_from='alerts@autorelister.com.au')
        captured = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            captured['url'] = req.full_url
            captured['auth'] = req.get_header('Authorization')
            captured['date'] = req.get_header('X-amz-date')
            captured['body'] = req.data.decode()
            return Resp()
        with mock.patch.object(uc.urllib.request, 'urlopen', fake_urlopen):
            ok = uc.send_email(c, 'Redis down', 'body text', transport='ses')
        self.assertTrue(ok)
        self.assertEqual(captured['url'], 'https://email.ap-southeast-2.amazonaws.com/')
        self.assertTrue(captured['auth'].startswith('AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/'))
        self.assertIn('/ap-southeast-2/ses/aws4_request, SignedHeaders=content-type;host;x-amz-date, Signature=', captured['auth'])
        self.assertRegex(captured['date'], r'^\d{8}T\d{6}Z$')
        self.assertIn('Action=SendEmail', captured['body'])
        self.assertIn('Source=alerts%40autorelister.com.au', captured['body'])
        self.assertIn('Destination.ToAddresses.member.1=kazp%40citysubaru.com.au', captured['body'])
        self.assertIn('Destination.ToAddresses.member.2=team%40example.com', captured['body'])
        self.assertIn('Message.Subject.Data=Redis+down', captured['body'])
        self.assertNotIn('fp.example', captured['url'])

    def test_sigv4_matches_known_vector(self):
        # Known vector: this signature was cross-checked against botocore's
        # SigV4Auth for the identical request (same body, host, date, keys).
        auth = uc._sigv4_authorization('POST', 'email.us-east-1.amazonaws.com', '/', b'Action=SendEmail',
                                       'us-east-1', 'ses', 'AKIDEXAMPLE', 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY',
                                       '20260101T000000Z')
        self.assertEqual(
            auth,
            'AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260101/us-east-1/ses/aws4_request, '
            'SignedHeaders=content-type;host;x-amz-date, '
            'Signature=b4ad1d06cb917288fc3b90feefc1195c9e91973fa6d5711462898c58476fb05f',
        )

    def test_ses_unconfigured_falls_back_to_flashpost_with_warning(self):
        sent_to = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            sent_to['url'] = req.full_url
            return Resp()
        with mock.patch.object(uc.urllib.request, 'urlopen', fake_urlopen):
            ok = uc.send_email(self.conf, 's', 'b', transport='ses')
        self.assertTrue(ok)
        self.assertEqual(sent_to['url'], 'https://fp.example/v1/send')

    def test_default_config_has_redis_target_on_ses(self):
        with mock.patch.dict(os.environ, {'UPTIME_ENV_FILE': os.path.join(self.tmp.name, 'none.env')}, clear=False):
            c = uc.cfg()
        redis = [t for t in c['targets'] if t[0] == 'redis'][0]
        self.assertIn('/api/team-alerts/health/?component=redis', redis[1])
        self.assertTrue(redis[2](200) and not redis[2](503))
        self.assertEqual(redis[3], 'ses')

    def test_env_file_loader(self):
        path = os.path.join(self.tmp.name, 'uptime.env')
        with open(path, 'w') as fh:
            fh.write('# comment\nKAZ_AND_TEAM=a@x.com, b@x.com\nUPTIME_FAILURES_BEFORE_ALERT="3"\n')
        with mock.patch.dict(os.environ, {'UPTIME_ENV_FILE': path}, clear=False):
            for k in ('KAZ_AND_TEAM', 'UPTIME_FAILURES_BEFORE_ALERT'):
                os.environ.pop(k, None)
            c = uc.cfg()
        self.assertEqual(c['recipients'], ['a@x.com', 'b@x.com'])
        self.assertEqual(c['failures_before_alert'], 3)


if __name__ == '__main__':
    unittest.main()
