"""Gumtree listing-count monitor: 0 listings or < 50% of the previous run →
Kaz-only alert via the central sender (one per dealer/profile); failed fetches
skipped; no repeats while the condition persists; [RESOLVED] after a normal run.

Run: python src/manage.py test team_alerts.tests_gumtree_listing_monitor
"""
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from team_alerts import alerting
from team_alerts import gumtree_listing_monitor as gm
from team_alerts.models import TeamAlertLog
from VehicleListing.models import GumtreeSyncRun

KAZ = ['kazp@citysubaru.com.au']


class GumtreeListingMonitorTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ctx = override_settings(
            KAZ_ONLY=KAZ, GUMTREE_DROP_ALERT_RATIO=0.5,
            ALERT_STATE_FILE=os.path.join(self.tmp.name, 'alerts.json'), ALERT_REMIND_HOURS=24,
        )
        ctx.enable()
        self.addCleanup(ctx.disable)
        self.dealer = User.objects.create_user(email='dealer@example.com', password='x', dealership_name='Motoroo')
        self.other = User.objects.create_user(email='other@example.com', password='x', dealership_name='CarWow')
        self.t0 = timezone.now()

    def _run(self, user, count, hours_ago, outcome=None, seller='111', source='cron'):
        outcome = outcome or ('empty' if count == 0 else 'success')
        run = GumtreeSyncRun.objects.create(user=user, seller_profile_id=seller, outcome=outcome, source=source, fetched_count=count)
        GumtreeSyncRun.objects.filter(pk=run.pk).update(created_at=self.t0 - timedelta(hours=hours_ago))
        return GumtreeSyncRun.objects.get(pk=run.pk)

    def _monitor(self, since_hours_ago=1, send_side_effect=None):
        with mock.patch.object(alerting, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            email_cls.return_value.send.side_effect = send_side_effect
            summary = gm.monitor_gumtree_listing_counts(since=self.t0 - timedelta(hours=since_hours_ago))
        return summary, email_cls

    # ── rule ────────────────────────────────────────────────────────────────
    def test_rule(self):
        self.assertTrue(gm.evaluate_counts(0, 40, 0.5)[0])
        self.assertTrue(gm.evaluate_counts(0, None, 0.5)[0])
        self.assertTrue(gm.evaluate_counts(19, 40, 0.5)[0])
        self.assertFalse(gm.evaluate_counts(20, 40, 0.5)[0])  # exactly 50% is not "fewer than"
        self.assertFalse(gm.evaluate_counts(45, 40, 0.5)[0])
        self.assertFalse(gm.evaluate_counts(5, None, 0.5)[0])  # no baseline, non-zero

    # ── batch behaviour ─────────────────────────────────────────────────────
    def test_stable_counts_send_nothing(self):
        self._run(self.dealer, 40, hours_ago=5)
        self._run(self.dealer, 38, hours_ago=0.5)
        summary, email = self._monitor()
        self.assertEqual(summary['alerts'], 0)
        email.assert_not_called()

    def test_drop_and_zero_alert_kaz_per_dealer_and_are_logged(self):
        self._run(self.dealer, 40, hours_ago=5)
        self._run(self.dealer, 15, hours_ago=0.5)          # 37% → drop
        self._run(self.other, 12, hours_ago=5, seller='222')
        self._run(self.other, 0, hours_ago=0.5, seller='222')  # zero
        summary, email = self._monitor()
        self.assertEqual(summary['alerts'], 2)
        self.assertEqual(email.call_count, 2)
        subjects = [c.kwargs['subject'] for c in email.call_args_list]
        self.assertTrue(any('Motoroo: 15 listings (dropped to 15 from 40)' in s for s in subjects))
        self.assertTrue(any('CarWow: 0 listings (returned 0 listings)' in s for s in subjects))
        for c in email.call_args_list:
            self.assertEqual(c.kwargs['to'], KAZ)
        email.return_value.send.assert_called_with(fail_silently=False)
        logs = TeamAlertLog.objects.filter(alert_type='gumtree_listing_drop').order_by('user_id')
        self.assertEqual([(l.user_id, l.active_listings, l.inactive_listings) for l in logs],
                         [(self.dealer.id, 15, 40), (self.other.id, 0, 12)])

    def test_failed_fetch_is_skipped_not_zero(self):
        self._run(self.dealer, 40, hours_ago=5)
        self._run(self.dealer, 0, hours_ago=0.5, outcome='failed')
        summary, email = self._monitor()
        self.assertEqual(summary['failed_runs'], 1)
        email.assert_not_called()

    # ── failed twice in a row ───────────────────────────────────────────────
    def test_two_failures_in_a_row_alert_kaz_once_until_success(self):
        self._run(self.dealer, 40, hours_ago=20)
        self._run(self.dealer, 0, hours_ago=15, outcome='failed')      # 1st failure → nothing
        summary, email = self._monitor(since_hours_ago=16)
        self.assertEqual((summary['failed_runs'], summary['fetch_alerts']), (1, 0))
        email.assert_not_called()
        run2 = self._run(self.dealer, 0, hours_ago=10, outcome='failed')  # 2nd in a row → alert
        GumtreeSyncRun.objects.filter(pk=run2.pk).update(http_status=402, error_detail='count request HTTP 402')
        summary, email = self._monitor(since_hours_ago=11)
        self.assertEqual(summary['fetch_alerts'], 1)
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], KAZ)
        self.assertEqual(kwargs['subject'], '[ALERT] Gumtree check failing — Motoroo: 2 failed fetches in a row')
        self.assertIn('HTTP 402', kwargs['body'])
        email.return_value.send.assert_called_once_with(fail_silently=False)
        self._run(self.dealer, 0, hours_ago=5, outcome='failed')        # 3rd → suppressed, no repeat
        summary, email = self._monitor(since_hours_ago=6)
        self.assertEqual(summary['fetch_alerts'], 0)
        email.assert_not_called()
        self._run(self.dealer, 38, hours_ago=0.5)                       # succeeds again → [RESOLVED] only
        _, email = self._monitor()
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED] Gumtree check failing — Motoroo'))
        self._run(self.dealer, 0, hours_ago=0.3, outcome='failed')      # streak restarts at 1 → nothing
        _, email = self._monitor()
        email.assert_not_called()

    def test_failure_streak_is_per_dealer_and_profile(self):
        self._run(self.dealer, 0, hours_ago=10, outcome='failed', seller='111')
        self._run(self.dealer, 0, hours_ago=10, outcome='failed', seller='333')
        self._run(self.other, 0, hours_ago=10, outcome='failed', seller='222')
        self._run(self.dealer, 0, hours_ago=0.5, outcome='failed', seller='111')   # 2nd for 111 only
        self._run(self.dealer, 12, hours_ago=0.5, seller='333')
        self._run(self.other, 9, hours_ago=0.5, seller='222')
        summary, email = self._monitor()
        self.assertEqual(summary['fetch_alerts'], 1)
        email.assert_called_once()
        self.assertIn('Gumtree profile: 111', email.call_args.kwargs['body'])

    def test_failed_run_is_never_the_baseline(self):
        self._run(self.dealer, 40, hours_ago=10)
        self._run(self.dealer, 0, hours_ago=5, outcome='failed')
        self._run(self.dealer, 15, hours_ago=0.5)
        summary, email = self._monitor()
        self.assertEqual(summary['alerts'], 1)  # compared with 40, not with the failed run
        self.assertIn('from 40', email.call_args.kwargs['body'])

    def test_first_run_without_baseline_only_alerts_on_zero(self):
        self._run(self.dealer, 7, hours_ago=0.5)
        summary, email = self._monitor()
        self.assertEqual((summary['no_baseline'], summary['alerts']), (1, 0))
        self._run(self.other, 0, hours_ago=0.5, seller='222')
        summary, email = self._monitor()
        self.assertEqual(summary['alerts'], 1)

    def test_persisting_zero_is_not_repeated_then_resolves_and_realerts(self):
        self._run(self.dealer, 30, hours_ago=20)
        self._run(self.dealer, 0, hours_ago=15)
        _, email = self._monitor(since_hours_ago=16)
        email.assert_called_once()
        self._run(self.dealer, 0, hours_ago=10)
        summary, email = self._monitor(since_hours_ago=11)
        self.assertEqual(summary['suppressed'], 1)
        email.assert_not_called()
        self._run(self.dealer, 25, hours_ago=5)   # back to normal → [RESOLVED]
        _, email = self._monitor(since_hours_ago=6)
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[RESOLVED] Gumtree listings dropped'))
        self._run(self.dealer, 0, hours_ago=0.5)  # drops again
        _, email = self._monitor()
        email.assert_called_once()
        self.assertTrue(email.call_args.kwargs['subject'].startswith('[ALERT]'))

    def test_profiles_compared_separately_and_only_this_batch(self):
        self._run(self.dealer, 40, hours_ago=5, seller='111')
        self._run(self.dealer, 5, hours_ago=5, seller='333')
        self._run(self.dealer, 39, hours_ago=0.5, seller='111')
        self._run(self.dealer, 5, hours_ago=0.5, seller='333')
        summary, email = self._monitor()
        self.assertEqual(summary['checked'], 2)
        email.assert_not_called()
        self._run(self.dealer, 1, hours_ago=0.2, seller='333')  # 20% of 5
        _, email = self._monitor()
        email.assert_called_once()
        self.assertIn('333', email.call_args.kwargs['body'])

    def test_manual_runs_are_not_evaluated_but_count_as_baseline(self):
        self._run(self.dealer, 40, hours_ago=5, source='manual')
        self._run(self.dealer, 10, hours_ago=0.5)
        summary, email = self._monitor()
        self.assertEqual(summary['alerts'], 1)
        self._run(self.other, 0, hours_ago=0.3, seller='222', source='manual')
        summary, email = self._monitor()
        self.assertEqual(summary['checked'], 1)

    def test_failed_send_logs_nothing_so_next_run_retries(self):
        self._run(self.dealer, 40, hours_ago=5)
        self._run(self.dealer, 0, hours_ago=0.5)
        summary, _ = self._monitor(send_side_effect=Exception('flashpost 500'))
        self.assertEqual(summary['alerts'], 0)
        self.assertEqual(TeamAlertLog.objects.count(), 0)
        _, email = self._monitor()
        email.assert_called_once()
