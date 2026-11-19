"""Host disk/memory monitoring: 80%/90% disk levels, memory low for 10 min, Kaz recipient.

Run: python src/manage.py test team_alerts.tests_host_monitor
"""
import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from team_alerts import host_monitor as hm


class HostMonitorTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            HOST_MONITOR_STATE_FILE=os.path.join(self.tmp.name, 'state.json'),
            HOST_MONITOR_ALERT_EMAIL='kazp@citysubaru.com.au',
            HOST_MONITOR_INTERVAL_MINUTES=5,
            HOST_MONITOR_DISK_WARN_PCT=80,
            HOST_MONITOR_DISK_CRIT_PCT=90,
            HOST_MONITOR_MEMORY_LOW_MB=300,
            HOST_MONITOR_MEMORY_LOW_MINUTES=10,
            HOST_MONITOR_DISK_PATHS=['/host'],
        )
        ctx.enable()
        self.addCleanup(ctx.disable)

    def _run(self, disk_pct=50.0, mem_mb=2000.0):
        with mock.patch.object(hm.os.path, 'isdir', return_value=True), \
                mock.patch.object(hm, 'read_disk_usage', return_value=(disk_pct, 40.0, 80.0)), \
                mock.patch.object(hm, 'read_memory_available_mb', return_value=mem_mb), \
                mock.patch.object(hm, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            result = hm.host_resource_monitor()
        return result, email_cls

    # ── disk ────────────────────────────────────────────────────────────────
    def test_disk_below_80_sends_nothing(self):
        _, email = self._run(disk_pct=79.9)
        email.assert_not_called()

    def test_disk_80_sends_warning_once(self):
        _, email = self._run(disk_pct=82)
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], ['kazp@citysubaru.com.au'])
        self.assertTrue(kwargs['subject'].startswith('[WARNING]'))
        email.return_value.send.assert_called_once_with(fail_silently=False)
        _, email2 = self._run(disk_pct=85)  # still warning — no repeat
        email2.assert_not_called()

    def test_disk_90_sends_critical_after_warning(self):
        self._run(disk_pct=82)
        _, email = self._run(disk_pct=91)
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[CRITICAL]'))
        _, email2 = self._run(disk_pct=95)  # still critical — no repeat
        email2.assert_not_called()

    def test_disk_recovery_rearms_alert(self):
        self._run(disk_pct=82)
        self._run(disk_pct=70)  # back to ok
        _, email = self._run(disk_pct=83)  # rises again → warns again
        email.assert_called_once()

    def test_disk_failed_send_is_retried_next_run(self):
        with mock.patch.object(hm.os.path, 'isdir', return_value=True), \
                mock.patch.object(hm, 'read_disk_usage', return_value=(85.0, 1.0, 2.0)), \
                mock.patch.object(hm, 'read_memory_available_mb', return_value=2000.0), \
                mock.patch.object(hm, 'EmailMessage') as email_cls:
            email_cls.return_value.send.side_effect = Exception('flashpost 500')
            hm.host_resource_monitor()
        _, email = self._run(disk_pct=85)
        email.assert_called_once()

    # ── memory ──────────────────────────────────────────────────────────────
    def test_memory_low_for_one_check_does_not_alert(self):
        _, email = self._run(mem_mb=250)
        email.assert_not_called()

    def test_memory_low_for_10_minutes_alerts_once(self):
        self._run(mem_mb=250)  # 5 min
        _, email = self._run(mem_mb=240)  # 10 min
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], ['kazp@citysubaru.com.au'])
        self.assertIn('memory low', kwargs['subject'])
        _, email2 = self._run(mem_mb=230)  # still low — no repeat
        email2.assert_not_called()

    def test_memory_recovery_resets_counter(self):
        self._run(mem_mb=250)
        self._run(mem_mb=1000)  # recovered
        _, email = self._run(mem_mb=250)  # 1 low again — not 10 min yet
        email.assert_not_called()

    # ── host-level readers ──────────────────────────────────────────────────
    def test_meminfo_parser_reads_memavailable(self):
        path = os.path.join(self.tmp.name, 'meminfo')
        with open(path, 'w') as fh:
            fh.write('MemTotal:       4000000 kB\nMemFree:         100000 kB\nMemAvailable:    307200 kB\n')
        self.assertEqual(hm.read_memory_available_mb(path), 300.0)

    def test_unmounted_disk_path_is_skipped(self):
        with mock.patch.object(hm.os.path, 'isdir', return_value=False), \
                mock.patch.object(hm, 'read_memory_available_mb', return_value=2000.0), \
                mock.patch.object(hm, 'EmailMessage') as email_cls:
            result = hm.host_resource_monitor()
        self.assertEqual(result['disk'], {})
        email_cls.assert_not_called()
