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

Alerts go to KAZ_AND_TEAM (comma-separated in .env) through the existing
Flashpost backend. A task is emailed ONCE when it becomes overdue (all newly
overdue tasks in one email); nothing more is sent while it stays overdue. When
the task runs again (last_run_at moves forward) the alert is re-armed, so a
later outage alerts again. Emails use fail_silently=False: a Flashpost failure
is logged and retried on the next run — never marked sent unless accepted.

Limitation: this runs under Beat too. If Beat itself is dead nothing runs,
including this monitor — the DB/Redis and host checks cover the server side.
"""
import json
import logging
import os
import tempfile

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

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


def _recipients():
    raw = _setting('KAZ_AND_TEAM', ['kazp@citysubaru.com.au'])
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(',')]
    return [r for r in raw if r]


def _state_file():
    return _setting('SCHEDULE_MONITOR_STATE_FILE', os.path.join(tempfile.gettempdir(), 'relister_schedule_monitor.json'))


def _load_state():
    try:
        with open(_state_file(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"schedule_monitor: could not read state file: {exc}")
    return {}


def _save_state(state):
    path = _state_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception as exc:
        logger.error(f"schedule_monitor: could not write state file {path}: {exc}")


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


def _send_alert(overdue_items, grace_minutes):
    to = _recipients()
    now = timezone.now().strftime('%Y-%m-%d %H:%M UTC')
    names = ', '.join(i['name'] for i in overdue_items)
    subject = f"[ALERT] Relister scheduled task(s) did not run: {names}"
    lines = [
        f"{len(overdue_items)} scheduled task(s) have not run within their expected time plus a {grace_minutes}-minute grace period.",
        '',
    ]
    for i in overdue_items:
        lines += [
            f"Task: {i['name']}  ({i['task']})",
            f"  Schedule: {i['schedule']}",
            f"  {i['anchor_label'].capitalize()}: {i['anchor']}",
            f"  Expected by: {i['due']}",
            f"  Overdue by: {i['overdue_minutes']} minutes",
            '',
        ]
    lines += [
        'Check that the Celery Beat scheduler and the scheduling-queue worker are running and the task is not erroring.',
        'This is sent once per task per outage; it re-arms when the task runs again.',
        f'Checked at: {now}',
    ]
    try:
        sent = EmailMessage(
            subject=subject,
            body='\n'.join(lines) + '\n',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=to,
        ).send(fail_silently=False)
        if sent != 1:
            logger.error(f"schedule_monitor: Flashpost reported {sent} sent for '{subject}' — will retry next run.")
            return False
        logger.info(f"schedule_monitor: alert sent to {to}: {names}")
        return True
    except Exception as exc:
        logger.error(f"schedule_monitor: FAILED to send alert to {to}: {exc} — will retry next run.")
        return False


@shared_task(queue='scheduling_queue')
def scheduled_task_monitor():
    from datetime import timedelta
    from django_celery_beat.models import PeriodicTask

    names = _setting('SCHEDULE_MONITOR_TASK_NAMES', DEFAULT_TASK_NAMES)
    grace_minutes = int(_setting('SCHEDULE_MONITOR_GRACE_MINUTES', 120))
    grace = timedelta(minutes=grace_minutes)
    now = timezone.now()

    state = _load_state()
    tasks_state = state.setdefault('tasks', {})
    summary = {}
    newly_overdue = []

    rows = {pt.name: pt for pt in PeriodicTask.objects.filter(name__in=names)}
    for name in names:
        pt = rows.get(name)
        if pt is None:
            logger.warning(f"schedule_monitor: periodic task '{name}' not found in Beat — skipped.")
            summary[name] = 'missing'
            continue
        if not pt.enabled:
            summary[name] = 'disabled'
            tasks_state.pop(name, None)
            continue

        try:
            due, anchor, anchor_label = expected_due_time(pt, now)
        except Exception as exc:
            logger.error(f"schedule_monitor: could not compute schedule for '{name}': {exc}")
            summary[name] = 'error'
            continue

        entry = tasks_state.get(name) or {}
        last_seen_run = entry.get('last_run_at')
        current_run = pt.last_run_at.isoformat() if pt.last_run_at else None
        alerted = bool(entry.get('alerted'))

        # The task ran since we alerted → it recovered; re-arm for a future outage.
        if alerted and current_run != last_seen_run:
            logger.info(f"schedule_monitor: '{name}' ran again at {current_run} — alert re-armed.")
            alerted = False

        overdue = now > due + grace
        if overdue:
            overdue_minutes = int((now - due).total_seconds() // 60)
            summary[name] = f'overdue {overdue_minutes}m'
            if not alerted:
                newly_overdue.append({
                    'name': name, 'task': pt.task, 'schedule': str(pt.schedule),
                    'anchor': anchor.strftime('%Y-%m-%d %H:%M UTC'), 'anchor_label': anchor_label,
                    'due': due.strftime('%Y-%m-%d %H:%M UTC'), 'overdue_minutes': overdue_minutes,
                })
            else:
                logger.warning(f"schedule_monitor: '{name}' still overdue by {overdue_minutes}m (already alerted).")
        else:
            summary[name] = 'ok'

        tasks_state[name] = {
            'last_run_at': current_run,
            'alerted': alerted,
            'overdue': overdue,
            'checked_at': now.isoformat(),
        }

    if newly_overdue:
        for i in newly_overdue:
            logger.error(f"schedule_monitor: '{i['name']}' OVERDUE — expected by {i['due']}, {i['overdue_minutes']}m late.")
        if _send_alert(newly_overdue, grace_minutes):
            for i in newly_overdue:
                tasks_state[i['name']]['alerted'] = True

    _save_state(state)
    logger.info(f"schedule_monitor: {summary}")
    return summary
