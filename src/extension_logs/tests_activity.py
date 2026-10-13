from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import activity
from .models import DailyActiveUser

PERTH = ZoneInfo('Australia/Perth')
User = get_user_model()


def perth(y, m, d, h=9, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=PERTH)


class RecordActivityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dealer@example.com', password='x')

    def test_first_open_sets_first_and_last_seen(self):
        t = perth(2026, 10, 13, 8, 0)
        row = activity.record_activity(self.user, 'open', now=t)
        self.assertTrue(row.active)
        self.assertEqual(row.first_seen, t)
        self.assertEqual(row.last_seen, t)
        self.assertEqual(row.action_count, 0)

    def test_reopen_updates_last_seen_keeps_first_seen(self):
        t1, t2 = perth(2026, 10, 13, 8, 0), perth(2026, 10, 13, 15, 30)
        activity.record_activity(self.user, 'open', now=t1)
        row = activity.record_activity(self.user, 'open', now=t2)
        self.assertEqual(row.first_seen, t1)
        self.assertEqual(row.last_seen, t2)
        self.assertEqual(DailyActiveUser.objects.filter(user=self.user).count(), 1)

    def test_actions_count_per_type_and_total(self):
        t = perth(2026, 10, 13, 10, 0)
        activity.record_activity(self.user, 'listed', now=t)
        activity.record_activity(self.user, 'listed', count=2, now=t)
        activity.record_activity(self.user, 'relisted', now=t)
        activity.record_activity(self.user, 'deleted', count=3, now=t)
        row = activity.record_activity(self.user, 'other', now=t)
        self.assertEqual((row.listed_count, row.relisted_count, row.deleted_count, row.other_count), (3, 1, 3, 1))
        self.assertEqual(row.action_count, 8)

    def test_action_without_open_still_marks_seen(self):
        t = perth(2026, 10, 13, 11, 0)
        row = activity.record_activity(self.user, 'listed', now=t)
        self.assertTrue(row.active)
        self.assertEqual(row.first_seen, t)
        self.assertEqual(row.last_seen, t)

    def test_new_perth_day_starts_a_new_row(self):
        # 23:50 and 00:10 Perth are different days even though UTC is the same day.
        activity.record_activity(self.user, 'listed', now=perth(2026, 10, 12, 23, 50))
        row = activity.record_activity(self.user, 'listed', now=perth(2026, 10, 13, 0, 10))
        self.assertEqual(row.listed_count, 1)
        self.assertEqual(DailyActiveUser.objects.filter(user=self.user).count(), 2)

    def test_users_are_tracked_separately(self):
        other = User.objects.create_user(email='other@example.com', password='x')
        t = perth(2026, 10, 13)
        activity.record_activity(self.user, 'listed', now=t)
        row = activity.record_activity(other, 'deleted', now=t)
        self.assertEqual((row.listed_count, row.deleted_count), (0, 1))

    def test_invalid_event_and_count_rejected(self):
        for event, count in [('bogus', 1), ('listed', 0), ('listed', 101), ('listed', 'two'), ('listed', True)]:
            with self.assertRaises(ValueError):
                activity.record_activity(self.user, event, count)
        self.assertFalse(DailyActiveUser.objects.exists())

    def test_summary_fills_missing_days_as_inactive(self):
        today = perth(2026, 10, 13).date()
        activity.record_activity(self.user, 'listed', count=4, now=perth(2026, 10, 13))
        activity.record_activity(self.user, 'open', now=perth(2026, 10, 11))
        summary = activity.daily_summary(self.user, days=14, today=today)
        self.assertEqual(len(summary['usage']), 14)
        self.assertEqual(summary['usage'][0]['date'], '2026-10-13')
        self.assertEqual(summary['usage'][-1]['date'], (today - timedelta(days=13)).isoformat())
        by_date = {d['date']: d for d in summary['usage']}
        self.assertTrue(by_date['2026-10-13']['active'])
        self.assertFalse(by_date['2026-10-12']['active'])
        self.assertTrue(by_date['2026-10-11']['active'])
        self.assertEqual(summary['totals']['listed_count'], 4)
        self.assertEqual(summary['totals']['active_days'], 2)

    def test_summary_days_clamped(self):
        today = perth(2026, 10, 13).date()
        self.assertEqual(len(activity.daily_summary(self.user, days=0, today=today)['usage']), 1)
        self.assertEqual(len(activity.daily_summary(self.user, days=999, today=today)['usage']), activity.MAX_SUMMARY_DAYS)


class ActivityApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='dealer@example.com', password='x')
        self.admin = User.objects.create_user(email='admin@example.com', password='x', is_staff=True)
        self.client = APIClient()

    def test_requires_authentication(self):
        self.assertIn(self.client.post('/api/extension-logs/activity/', {'event': 'open'}, format='json').status_code, (401, 403))
        self.assertIn(self.client.get('/api/extension-logs/activity/').status_code, (401, 403))

    def test_post_open_then_listed_and_get_summary(self):
        self.client.force_authenticate(self.user)
        r = self.client.post('/api/extension-logs/activity/', {'event': 'open'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['today']['active'])
        r = self.client.post('/api/extension-logs/activity/', {'event': 'listed', 'count': 2}, format='json')
        self.assertEqual(r.data['today']['listed_count'], 2)
        r = self.client.get('/api/extension-logs/activity/?days=7')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data['usage']), 7)
        self.assertEqual(r.data['totals']['listed_count'], 2)

    def test_post_invalid_event_is_400(self):
        self.client.force_authenticate(self.user)
        r = self.client.post('/api/extension-logs/activity/', {'event': 'nope'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_admin_endpoint(self):
        activity.record_activity(self.user, 'deleted')
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(f'/api/extension-logs/admin/activity/?user_id={self.user.id}').status_code, 403)
        self.client.force_authenticate(self.admin)
        r = self.client.get('/api/extension-logs/admin/activity/?email=dealer@example.com&days=3')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['user_id'], self.user.id)
        self.assertEqual(r.data['totals']['deleted_count'], 1)
        self.assertEqual(self.client.get('/api/extension-logs/admin/activity/').status_code, 400)
