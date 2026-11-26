"""The Beat schedule lives in PeriodicTask rows created by the post_migrate
receiver in VehicleListing/apps.py. That receiver used to be weak-referenced
and garbage-collected, so `migrate` created none of the rows.

Run: python src/manage.py test VehicleListing.tests_beat_tasks --settings=relister.settings_test
"""
import gc

from django.apps import apps
from django.db.models.signals import post_migrate
from django.test import TestCase
from django_celery_beat.models import PeriodicTask

EXPECTED_TASKS = {
    'Clean_30_days_Old_logs',
    'Check_Gumtree_Profile_Re-Listings',
    'Check_Custom_Domain_Profile_Re-Listings',
    'Check_Trial_Expiry',
    'Mark_Overdue_Invoices',
    'Check_Subscription_Renewal_Warnings',
    'Report_Listing_Overage_Usage',
    'Infrastructure_Health_Check',
    'Host_Resource_Monitor',
    'Scheduled_Task_Monitor',
    'ZenRows_Usage_Monitor',
    'Failure_Spike_Monitor',
    'RDS_Backup_Monitor',
    'Weekly_Alert_Test_Email',
}


class BeatTasksCreatedOnMigrateTests(TestCase):
    def test_receiver_survives_gc_and_creates_every_beat_row(self):
        gc.collect()  # would have killed the old weak-referenced receiver
        self.assertTrue(
            post_migrate.has_listeners(),
            'post_migrate has no receivers — setup_periodic_tasks was garbage-collected',
        )
        PeriodicTask.objects.all().delete()
        app_config = apps.get_app_config('VehicleListing')
        post_migrate.send(
            sender=app_config, app_config=app_config, verbosity=0,
            interactive=False, using='default', plan=[], apps=apps,
        )
        names = set(PeriodicTask.objects.filter(enabled=True).values_list('name', flat=True))
        self.assertEqual(names, EXPECTED_TASKS)
        self.assertEqual(PeriodicTask.objects.filter(crontab__isnull=True).count(), 0)

    def test_other_apps_migrate_signal_is_ignored(self):
        PeriodicTask.objects.all().delete()
        other = apps.get_app_config('accounts')
        post_migrate.send(
            sender=other, app_config=other, verbosity=0,
            interactive=False, using='default', plan=[], apps=apps,
        )
        self.assertEqual(PeriodicTask.objects.count(), 0)
