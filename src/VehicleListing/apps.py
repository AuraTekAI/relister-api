from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.dispatch import receiver


class VehiclelistingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'VehicleListing'
    
    def ready(self):
        # Connect the post_migrate signal to a handler function
        post_migrate.connect(create_periodic_task, sender=self)

@receiver(post_migrate)
def create_periodic_task(sender, **kwargs):
    from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule
    
    schedule, created = IntervalSchedule.objects.get_or_create(
        every=1,
        period=IntervalSchedule.HOURS,
    )
    PeriodicTask.objects.update_or_create(
        name="Create_Pending_Facebook_Listings",
        defaults={  
            "interval": schedule,
            "task": "VehicleListing.tasks.create_pending_facebook_marketplace_listing_task",
        },
    )

    schedule, created = IntervalSchedule.objects.get_or_create(
        every=1,
        period=IntervalSchedule.DAYS,
    )
    PeriodicTask.objects.update_or_create(
        name="Relist_7_Days_Facebook_Listings",
        defaults={
            "interval": schedule,
            "task": "VehicleListing.tasks.relist_facebook_marketplace_listing_task",
        },
    )

    schedule, created = IntervalSchedule.objects.get_or_create(
        every=12,
        period=IntervalSchedule.HOURS,
    )
    PeriodicTask.objects.update_or_create(
        name="Create_Failed_Facebook_Listings",
        defaults={
            "interval": schedule,
            "task": "VehicleListing.tasks.create_failed_facebook_marketplace_listing_task",
        },
    )

    crontab_schedule, _ = CrontabSchedule.objects.get_or_create(minute=0, hour='16-20')

    PeriodicTask.objects.update_or_create(
        name="Check_Facebook_Profile_Re-Listings",
        defaults={
            "crontab": crontab_schedule,
            "task": "VehicleListing.tasks.check_facebook_profile_relisting_task",
        },
    )

    PeriodicTask.objects.update_or_create(
        name="Check_Gumtree_Profile_Re-Listings",
        defaults={
            "crontab": crontab_schedule,
            "task": "VehicleListing.tasks.check_gumtree_profile_relisting_task",
        },
    )

    crontab_schedule, _ = CrontabSchedule.objects.get_or_create(minute=0, hour=0, day_of_month="last")
    PeriodicTask.objects.update_or_create(
        name="Send_invoices_to_user",
        defaults={
            "crontab": crontab_schedule,
            "task": "VehicleListing.tasks.generate_and_send_monthly_invoices",
            "interval": None,
        },
    )