"""
Scheduled-task monitoring — replaces the dropped Celery queue-backlog alert.

Every SCHEDULE_MONITOR_INTERVAL_MINUTES (Beat task "Scheduled_Task_Monitor")
this looks at the enabled django-celery-beat PeriodicTask rows listed in
SCHEDULE_MONITOR_TASK_NAMES (Gumtree check, custom-domain check, trial expiry,
renewal warnings, overdue invoices, overage report, log cleanup) and asks: has
each one run when its schedule said it should?

For each task the expected next run is derived from the task's OWN crontab:
    due = last_run_at + schedule.remaining_estimate(last_run_at)
(or from date_changed when it has never run). If now > due + grace period
(SCHEDULE_MONITOR_GRACE_MINUTES) the task is OVERDUE.

Alerts go to KAZ_AND_TEAM through team_alerts.alerting: one email when a task
first becomes overdue, a reminder after ALERT_REMIND_HOURS if it is still
overdue, and a [RESOLVED] email once it has run again.

Limitation: this runs under Beat too. If Beat itself is dead nothing runs,
including this monitor — the DB/Redis and host checks cover the server side.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .alerting import raise_alert, resolve_alert

logger = logging.getLogger('relister_views')

DEFAULT_TASK_NAMES = [
    'Check_Gumtree_Profile_Re-Listings',
    'Check_Custom_Domain_Profile_Re-Listings',
    'Check_Trial_Expiry',
    'Check_Subscription_Renewal_Warnings',
    'Mark_Overdue_Invoices',
    'Report_Listing_Overage_Usage',
    'Clean_30_days_Old_logs',
]


def _setting(name, default):
    return getattr(settings, name, default)


def expected_due_time(periodic_task, now):
    """
    When this task should have run by, from its own schedule:
    the first scheduled time strictly after its last run (or after the row was
    last changed/enabled if it has never run). Returns (due, anchor, anchor_label).
    """
    anchor = periodic_task.last_run_at
    label = 'last run'
    if anchor is None:
        anchor = periodic_task.date_changed or now
        label = 'enabled/changed'
    remaining = periodic_task.schedule.remaining_estimate(anchor)
    return anchor + remaining, anchor, label


@shared_task(queue='scheduling_queue')
def scheduled_task_monitor():
    from datetime import timedelta
    from django_celery_beat.models import PeriodicTask

    names = _setting('SCHEDULE_MONITOR_TASK_NAMES', DEFAULT_TASK_NAMES)
    grace_minutes = int(_setting('SCHEDULE_MONITOR_GRACE_MINUTES', 120))
    grace = timedelta(minutes=grace_minutes)
    now = timezone.now()
    summary = {}

    rows = {pt.name: pt for pt in PeriodicTask.objects.filter(name__in=names)}
    for name in names:
        pt = rows.get(name)
        key = f'schedule:{name}'
        if pt is None:
            logger.warning(f"schedule_monitor: periodic task '{name}' not found in Beat — skipped.")
            summary[name] = 'missing'
            continue
        if not pt.enabled:
            summary[name] = 'disabled'
            resolve_alert(key, note=f"Task {name} was disabled in Beat.")
            continue

        try:
            due, anchor, anchor_label = expected_due_time(pt, now)
        except Exception as exc:
            logger.error(f"schedule_monitor: could not compute schedule for '{name}': {exc}")
            summary[name] = 'error'
            continue

        if now <= due + grace:
            summary[name] = 'ok'
            resolve_alert(key, note=f"{name} ran at {pt.last_run_at.strftime('%Y-%m-%d %H:%M UTC') if pt.last_run_at else 'unknown'}.")
            continue

        overdue_minutes = int((now - due).total_seconds() // 60)
        summary[name] = f'overdue {overdue_minutes}m'
        logger.error(f"schedule_monitor: '{name}' OVERDUE — expected by {due:%Y-%m-%d %H:%M} UTC, {overdue_minutes}m late.")
        raise_alert(
            key, 'team',
            f"[ALERT] Relister scheduled task did not run: {name}",
            '\n'.join([
                f"The scheduled task {name} has not run within its expected time plus a {grace_minutes}-minute grace period.",
                '',
                f"Task: {name}  ({pt.task})",
                f"  Schedule: {pt.schedule}",
                f"  {anchor_label.capitalize()}: {anchor:%Y-%m-%d %H:%M} UTC",
                f"  Expected by: {due:%Y-%m-%d %H:%M} UTC",
                f"  Overdue by: {overdue_minutes} minutes",
                '',
                'Check that the Celery Beat scheduler and the scheduling-queue worker are running and the task is not erroring.',
            ]) + '\n',
        )

    logger.info(f"schedule_monitor: {summary}")
    return summary
