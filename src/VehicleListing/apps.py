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
    from django_celery_beat.models import PeriodicTask, IntervalSchedule
    
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
        every=1,
        period=IntervalSchedule.DAYS,
    )
    PeriodicTask.objects.update_or_create(
        name="Create_Failed_Facebook_Listings",
        defaults={
            "interval": schedule,
            "task": "VehicleListing.tasks.create_failed_facebook_marketplace_listing_task",
        },
    )
