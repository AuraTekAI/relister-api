"""Gumtree listing-count monitor: 0 listings or < 50% of the previous run →
one Kaz-only alert per batch; failed fetches are skipped; no repeats while the
condition persists; re-alerts after a normal run.

Run: python src/manage.py test team_alerts.tests_gumtree_listing_monitor
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from team_alerts import gumtree_listing_monitor as gm
from team_alerts.models import TeamAlertLog
from VehicleListing.models import GumtreeSyncRun

KAZ = ['kazp@citysubaru.com.au']


@override_settings(KAZ_ONLY=KAZ, GUMTREE_DROP_ALERT_RATIO=0.5)
class GumtreeListingMonitorTests(TestCase):
    def setUp(self):
        self.dealer = User.objects.create_user(email='dealer@example.com', password='x', dealership_name='Motoroo')
        self.other = User.objects.create_user(email='other@example.com', password='x', dealership_name='CarWow')
        self.t0 = timezone.now()

    def _run(self, user, count, hours_ago, outcome=None, seller='111', source='cron'):
        outcome = outcome or ('empty' if count == 0 else 'success')
        run = GumtreeSyncRun.objects.create(user=user, seller_profile_id=seller, outcome=outcome, source=source, fetched_count=count)
        GumtreeSyncRun.objects.filter(pk=run.pk).update(created_at=self.t0 - timedelta(hours=hours_ago))
        return GumtreeSyncRun.objects.get(pk=run.pk)

    def _monitor(self, since_hours_ago=1):
        since = self.t0 - timedelta(hours=since_hours_ago)
        with mock.patch.object(gm, 'EmailMessage') as email_cls:
            email_cls.return_value.send.return_value = 1
            summary = gm.monitor_gumtree_listing_counts(since=since)
        # Runs are backdated in these tests, so backdate the alert log the same
        # way (in production both are real time, in order).
        newest = GumtreeSyncRun.objects.filter(created_at__gte=since).order_by('-created_at').first()
        if newest:
            TeamAlertLog.objects.filter(alert_type=gm.ALERT_TYPE, sent_at__gte=self.t0 - timedelta(seconds=5)) \
                .update(sent_at=newest.created_at + timedelta(minutes=1))
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

    def test_drop_and_zero_alert_kaz_in_one_email_and_are_logged(self):
        self._run(self.dealer, 40, hours_ago=5)
        self._run(self.dealer, 15, hours_ago=0.5)          # 37% → drop
        self._run(self.other, 12, hours_ago=5, seller='222')
        self._run(self.other, 0, hours_ago=0.5, seller='222')  # zero
        summary, email = self._monitor()
        self.assertEqual(summary['alerts'], 2)
        email.assert_called_once()
        kwargs = email.call_args.kwargs
        self.assertEqual(kwargs['to'], KAZ)
        self.assertIn('2 dealer(s)', kwargs['subject'])
        self.assertIn('Motoroo', kwargs['body'])
        self.assertIn('dropped to 15 from 40', kwargs['body'])
        self.assertIn('returned 0 listings', kwargs['body'])
        email.return_value.send.assert_called_once_with(fail_silently=False)
        logs = TeamAlertLog.objects.filter(alert_type='gumtree_listing_drop').order_by('user_id')
        self.assertEqual([(l.user_id, l.active_listings, l.inactive_listings) for l in logs],
                         [(self.dealer.id, 15, 40), (self.other.id, 0, 12)])

    def test_failed_fetch_is_skipped_not_zero(self):
        self._run(self.dealer, 40, hours_ago=5)
        self._run(self.dealer, 0, hours_ago=0.5, outcome='failed')
        summary, email = self._monitor()
        self.assertEqual(summary['failed_runs'], 1)
        email.assert_not_called()

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

    def test_persisting_zero_is_not_repeated_but_realerts_after_recovery(self):
        self._run(self.dealer, 30, hours_ago=20)
        self._run(self.dealer, 0, hours_ago=15)
        _, email = self._monitor(since_hours_ago=16)
        email.assert_called_once()
        self._run(self.dealer, 0, hours_ago=10)
        summary, email = self._monitor(since_hours_ago=11)
        self.assertEqual(summary['suppressed'], 1)
        email.assert_not_called()
        self._run(self.dealer, 25, hours_ago=5)   # back to normal
        _, email = self._monitor(since_hours_ago=6)
        email.assert_not_called()
        self._run(self.dealer, 0, hours_ago=0.5)  # drops again
        _, email = self._monitor()
        email.assert_called_once()

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
        with mock.patch.object(gm, 'EmailMessage') as email_cls:
            email_cls.return_value.send.side_effect = Exception('flashpost 500')
            summary = gm.monitor_gumtree_listing_counts(since=self.t0 - timedelta(hours=1))
        self.assertEqual(summary['alerts'], 0)
        self.assertEqual(TeamAlertLog.objects.count(), 0)
        _, email = self._monitor()
        email.assert_called_once()
