# VehicleListing/apps.py

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate
from django.dispatch import receiver

class VehiclelistingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'VehicleListing'

    def ready(self):
        from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule
        import logging

        @receiver(post_migrate)
        def setup_periodic_tasks(sender, **kwargs):
            try:
                # Define shared intervals and crontabs only once
                interval_map = {
                    "hourly": IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)[0],
                    "daily": IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.DAYS)[0],
                    "twelve_hour": IntervalSchedule.objects.get_or_create(every=12, period=IntervalSchedule.HOURS)[0],
                }

                crontab_map = {
                    "evening": CrontabSchedule.objects.get_or_create(minute='0', hour='16-20')[0],
                    "last_day": CrontabSchedule.objects.get_or_create(minute='0', hour='0', day_of_month='last')[0],
                }

                tasks = [
                    {
                        "name": "Create_Pending_Facebook_Listings",
                        "task": "VehicleListing.tasks.create_pending_facebook_marketplace_listing_task",
                        "interval": interval_map["hourly"],
                        "crontab": None,
                    },
                    {
                        "name": "Relist_7_Days_Facebook_Listings",
                        "task": "VehicleListing.tasks.relist_facebook_marketplace_listing_task",
                        "interval": interval_map["daily"],
                        "crontab": None,
                    },
                    {
                        "name": "Create_Failed_Facebook_Listings",
                        "task": "VehicleListing.tasks.create_failed_facebook_marketplace_listing_task",
                        "interval": interval_map["twelve_hour"],
                        "crontab": None,
                    },
                    {
                        "name": "Check_Facebook_Profile_Re-Listings",
                        "task": "VehicleListing.tasks.check_facebook_profile_relisting_task",
                        "interval": None,
                        "crontab": crontab_map["evening"],
                    },
                    {
                        "name": "Check_Gumtree_Profile_Re-Listings",
                        "task": "VehicleListing.tasks.check_gumtree_profile_relisting_task",
                        "interval": None,
                        "crontab": crontab_map["evening"],
                    },
                    {
                        "name": "Send_invoices_to_user",
                        "task": "VehicleListing.tasks.generate_and_send_monthly_invoices",
                        "interval": None,
                        "crontab": crontab_map["last_day"],
                    },
                    {
                        "name": "Check_Images_Upload_Status",
                        "task": "VehicleListing.tasks.check_images_upload_status",
                        "interval": interval_map["daily"],
                        "crontab": None,
                    },
                    {
                        "name": "Reset_Listing_Count_for_Users",
                        "task": "VehicleListing.tasks.reset_listing_count_for_users_task",
                        "interval": interval_map["daily"],
                        "crontab": None,
                    },
                ]

                for t in tasks:
                    task_obj, created = PeriodicTask.objects.update_or_create(
                        name=t["name"],
                        defaults={
                            "task": t["task"],
                            "interval": t["interval"],
                            "crontab": t["crontab"],
                            "enabled": True,
                        }
                    )
                    action = "Created" if created else "Updated"
                    logging.info(f"{action} task: {t['name']}")

            except Exception as e:
                logging.warning(f"Periodic task setup skipped: {e}")







# # VehicleListing/apps.py

# from django.apps import AppConfig
# from django.db.utils import OperationalError, ProgrammingError

# class VehiclelistingConfig(AppConfig):
#     default_auto_field = 'django.db.models.BigAutoField'
#     name = 'VehicleListing'

#     def ready(self):
#         try:
#             from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule

#             # Define shared intervals and crontabs only once
#             interval_map = {
#                 "hourly": IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)[0],
#                 "daily": IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.DAYS)[0],
#                 "twelve_hour": IntervalSchedule.objects.get_or_create(every=12, period=IntervalSchedule.HOURS)[0],
#             }

#             crontab_map = {
#                 "evening": CrontabSchedule.objects.get_or_create(minute='0', hour='16-20')[0],
#                 "last_day": CrontabSchedule.objects.get_or_create(minute='0', hour='0', day_of_month='last')[0],
#             }

#             # Define all tasks
#             tasks = [
#                 {
#                     "name": "Create_Pending_Facebook_Listings",
#                     "task": "VehicleListing.tasks.create_pending_facebook_marketplace_listing_task",
#                     "interval": interval_map["hourly"],
#                     "crontab": None,
#                 },
#                 {
#                     "name": "Relist_7_Days_Facebook_Listings",
#                     "task": "VehicleListing.tasks.relist_facebook_marketplace_listing_task",
#                     "interval": interval_map["daily"],
#                     "crontab": None,
#                 },
#                 {
#                     "name": "Create_Failed_Facebook_Listings",
#                     "task": "VehicleListing.tasks.create_failed_facebook_marketplace_listing_task",
#                     "interval": interval_map["twelve_hour"],
#                     "crontab": None,
#                 },
#                 {
#                     "name": "Check_Facebook_Profile_Re-Listings",
#                     "task": "VehicleListing.tasks.check_facebook_profile_relisting_task",
#                     "interval": None,
#                     "crontab": crontab_map["evening"],
#                 },
#                 {
#                     "name": "Check_Gumtree_Profile_Re-Listings",
#                     "task": "VehicleListing.tasks.check_gumtree_profile_relisting_task",
#                     "interval": None,
#                     "crontab": crontab_map["evening"],
#                 },
#                 {
#                     "name": "Send_invoices_to_user",
#                     "task": "VehicleListing.tasks.generate_and_send_monthly_invoices",
#                     "interval": None,
#                     "crontab": crontab_map["last_day"],
#                 },
#                 {
#                     "name": "Check_Images_Upload_Status",
#                     "task": "VehicleListing.tasks.check_images_upload_status",
#                     "interval": interval_map["daily"],
#                     "crontab": None,
#                 },
#                 {
#                     "name": "Reset_Listing_Count_for_Users",
#                     "task": "VehicleListing.tasks.reset_listing_count_for_users_task",
#                     "interval": interval_map["daily"],
#                     "crontab": None,
#                 },
#             ]

#             # Create or update each task
#             for t in tasks:
#                 task_obj, created = PeriodicTask.objects.update_or_create(
#                     name=t["name"],
#                     defaults={
#                         "task": t["task"],
#                         "interval": t["interval"],
#                         "crontab": t["crontab"],
#                         "enabled": True,
#                     }
#                 )
#                 action = "Created" if created else "Updated"
#                 print(f"{action} task: {t['name']}")

#         except (OperationalError, ProgrammingError):
#             print("Database not ready, skipping task setup.")


