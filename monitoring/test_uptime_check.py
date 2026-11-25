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

    def _send(self, _conf, subject, body):
        self.sent.append((subject, body))
        return True

    def _run(self, website=200, api=405, api_error=None):
        def check(url, timeout):
            if 'site' in url:
                return (website, 0.1, None) if website else (None, timeout, 'timed out')
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

        def flaky(_c, subject, body):
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
