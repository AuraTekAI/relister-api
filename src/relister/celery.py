from celery import Celery, Task

from django.conf import settings

import os
import logging

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'relister.settings')

# Initialize a Celery application named 'relister'.
app = Celery('relister')
# Configure Celery using settings from the Django configuration file under the 'CELERY' namespace.
app.config_from_object('django.conf:settings', namespace='CELERY')
# Set 'task_ack_late' to True to acknowledge tasks after they are executed.
app.task_ack_late = True



class CustomExceptionHandler(Task):
    """
    Custom task class for handling exceptions in Celery tasks.
    This class overrides the 'on_failure' method to log detailed failure information.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        Log detailed information when a Celery task fails.

        Parameters:
        - exc: Exception raised during task execution.
        - task_id: ID of the failed task.
        - args: Positional arguments passed to the task.
        - kwargs: Keyword arguments passed to the task.
        - einfo: Exception info including traceback details.
        """
        print(f"Name : {self.name} - Task ID: {task_id}  ")

# Automatically discover tasks from all registered Django apps.
app.autodiscover_tasks()
