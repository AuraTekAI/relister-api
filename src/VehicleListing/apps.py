# VehicleListing/apps.py

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate
from django.dispatch import receiver

class VehiclelistingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'VehicleListing'


    def ready(self):
        from django_celery_beat.models import PeriodicTask, CrontabSchedule
        import logging

        @receiver(post_migrate)
        def setup_periodic_tasks(sender, **kwargs):
            try:
                # Define all required crontab times
                crontab_map = {
                    "twice_daily_9am_9pm": [
                        CrontabSchedule.objects.get_or_create(minute='0', hour='9')[0],
                        CrontabSchedule.objects.get_or_create(minute='0', hour='21')[0],
                    ],
                    "daily_12pm": CrontabSchedule.objects.get_or_create(minute='0', hour='12')[0],
                    "daily_4pm": CrontabSchedule.objects.get_or_create(minute='0', hour='16')[0],
                    "daily_6am": CrontabSchedule.objects.get_or_create(minute='0', hour='6')[0],
                    "daily_5am": CrontabSchedule.objects.get_or_create(minute='0', hour='5')[0],
                    "monthly_4am": CrontabSchedule.objects.get_or_create(minute='0', hour='4', day_of_month='last')[0],
                    "daily_6pm": CrontabSchedule.objects.get_or_create(minute='0', hour='18')[0],
                    "daily_3am": CrontabSchedule.objects.get_or_create(minute='0', hour='3')[0],
                    "daily_2am": CrontabSchedule.objects.get_or_create(minute='0', hour='2')[0],
                    "daily_1am": CrontabSchedule.objects.get_or_create(minute='0', hour='1')[0],
                    "daily_12am": CrontabSchedule.objects.get_or_create(minute='0', hour='0')[0],
                    "daily_10pm": CrontabSchedule.objects.get_or_create(minute='0', hour='22')[0],
                }

                tasks = [
                    # 9am
                    # {
                    #     "name": "Create_Pending_Facebook_Listings_Morning",
                    #     "task": "VehicleListing.tasks.create_pending_facebook_marketplace_listing_task",
                    #     "crontab": crontab_map["twice_daily_9am_9pm"][0],
                    # },
                    # # 9pm
                    # {
                    #     "name": "Create_Pending_Facebook_Listings_Evening",
                    #     "task": "VehicleListing.tasks.create_pending_facebook_marketplace_listing_task",
                    #     "crontab": crontab_map["twice_daily_9am_9pm"][1],
                    # },
                    # {
                    #     "name": "Relist_7_Days_Facebook_Listings",
                    #     "task": "VehicleListing.tasks.relist_facebook_marketplace_listing_task",
                    #     "crontab": crontab_map["daily_12pm"],
                    # },
                    # {
                    #     "name": "Create_Failed_Facebook_Listings",
                    #     "task": "VehicleListing.tasks.create_failed_facebook_marketplace_listing_task",
                    #     "crontab": crontab_map["daily_4pm"],
                    # },
                    {
                        "name": "Check_Facebook_Profile_Re-Listings",
                        "task": "VehicleListing.tasks.check_facebook_profile_relisting_task",
                        "crontab": crontab_map["daily_6am"],
                    },
                    {
                        "name": "Check_Gumtree_Profile_Re-Listings",
                        "task": "VehicleListing.tasks.check_gumtree_profile_relisting_task",
                        "crontab": crontab_map["daily_5am"],
                    },
                    {
                        "name": "Send_invoices_to_user",
                        "task": "VehicleListing.tasks.generate_and_send_monthly_invoices",
                        "crontab": crontab_map["monthly_4am"],
                    },
                    # {
                    #     "name": "Check_Images_Upload_Status",
                    #     "task": "VehicleListing.tasks.check_images_upload_status",
                    #     "crontab": crontab_map["daily_6pm"],
                    # },
                    # {
                    #     "name": "Reset_Listing_Count_for_Users",
                    #     "task": "VehicleListing.tasks.reset_listing_count_for_users_task",
                    #     "crontab": crontab_map["daily_3am"],
                    # },
                    {
                        "name": "Send_Daily_Activity_Report",
                        "task": "VehicleListing.tasks.send_daily_activity_report",
                        "crontab": crontab_map["daily_2am"],
                    },
                    {
                        "name": "Clean_30_days_Old_logs",
                        "task": "VehicleListing.tasks.cleanup_old_logs",
                        "crontab": crontab_map["daily_1am"],
                    },
                    # {
                    #     "name": "Delete_high_retry_listings",
                    #     "task": "VehicleListing.tasks.cleanup_high_retry_listings",
                    #     "crontab": crontab_map["daily_12am"],
                    # },
                    # {
                    #     "name": "Remove_Duplicate_Listings",
                    #     "task": "VehicleListing.tasks.remove_duplicate_listings_task",
                    #     "crontab": crontab_map["daily_10pm"],
                    # },
                    # {
                    #     "name": "Retry_Failed_Re_Listings",
                    #     "task": "VehicleListing.tasks.retry_failed_relistings",
                    #     "crontab": crontab_map["daily_3am"],
                    # },
                    
                ]

                for t in tasks:
                    task_obj, created = PeriodicTask.objects.update_or_create(
                        name=t["name"],
                        defaults={
                            "task": t["task"],
                            "crontab": t["crontab"],
                            "interval": None,
                            "enabled": True,
                        }
                    )
                    action = "Created" if created else "Updated"
                    logging.info(f"{action} task: {t['name']}")

            except Exception as e:
                logging.warning(f"Periodic task setup skipped: {e}")


    # def ready(self):
    #     from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule
    #     import logging

    #     @receiver(post_migrate)
    #     def setup_periodic_tasks(sender, **kwargs):
    #         try:
    #             # Define shared intervals and crontabs only once
    #             interval_map = {
    #                 "hourly": IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)[0],
    #                 "daily": IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.DAYS)[0],
    #                 "twelve_hour": IntervalSchedule.objects.get_or_create(every=12, period=IntervalSchedule.HOURS)[0],
    #             }

    #             crontab_map = {
    #                 "evening": CrontabSchedule.objects.get_or_create(minute='0', hour='16-20')[0],
    #                 "last_day": CrontabSchedule.objects.get_or_create(minute='0', hour='0', day_of_month='last')[0],
    #                 "fixed_time": CrontabSchedule.objects.get_or_create(minute='0', hour='16')[0],
    #             }

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
    #                 {
    #                     "name": "Send_Daily_Activity_Report",
    #                     "task": "VehicleListing.tasks.send_daily_activity_report",
    #                     "interval": None,
    #                     "crontab": crontab_map["fixed_time"],
    #                 },
    #             ]

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
    #                 logging.info(f"{action} task: {t['name']}")

    #         except Exception as e:
    #             logging.warning(f"Periodic task setup skipped: {e}")