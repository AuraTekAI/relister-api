"""
Infrastructure health check — runs every 5 minutes via Celery Beat
(registered in VehicleListing/apps.py as "Infrastructure_Health_Check").

Checks that the database accepts a query. (Redis is checked from outside —
see COMPONENTS below — because a Celery task cannot run while its broker is
down.) Each
component keeps its own consecutive-failure count in a small JSON state file
(NOT in the DB or Redis — those are exactly what may be down). After
HEALTHCHECK_FAILURES_BEFORE_ALERT failures in a row (default 2, i.e. ~10 min of
outage) the alert is raised through team_alerts.alerting to KAZ_AND_TEAM: one
email at once, a reminder only if still down after ALERT_REMIND_HOURS, and a
[RESOLVED] email when the component answers again. The failure count resets as
soon as the component recovers. A failed delivery is logged and retried on the
next run — it is never recorded as sent.
"""
import json
import logging
import os
import tempfile

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('relister_views')

# Only the database is checked from Celery. Redis is Celery's broker: when it is
# down this task can never run, so the Redis check is driven from the EXTERNAL
# uptime cron (monitoring/uptime_check.py) through the /api/team-alerts/health/
# endpoint (views.infrastructure_health), which pings Redis from the web process
# and whose alert goes out through the SES fallback, not Flashpost.
COMPONENTS = ('database',)


def _state_file():
    return getattr(
        settings, 'HEALTHCHECK_STATE_FILE',
        os.path.join(tempfile.gettempdir(), 'relister_infra_healthcheck.json'),
    )


def _load_state():
    try:
        with open(_state_file(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"infra_health_check: could not read state file: {exc}")
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
        logger.error(f"infra_health_check: could not write state file {path}: {exc}")


def _check_database():
    """Raise on failure; a real round-trip, not just a cached connection."""
    from django.db import connection
    connection.ensure_connection()
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        row = cursor.fetchone()
    if not row or row[0] != 1:
        raise RuntimeError('SELECT 1 returned an unexpected result')


def _check_redis():
    """Raise on failure; PING against the same Redis the app/Celery use."""
    import redis
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=5, socket_timeout=5)
    try:
        if client.ping() is not True:
            raise RuntimeError('PING did not return PONG')
    finally:
        try:
            client.close()
        except Exception:
            pass


CHECKS = {
    'database': _check_database,
    'redis': _check_redis,
}


def _alert_body(component, failures, error):
    interval = getattr(settings, 'HEALTHCHECK_INTERVAL_MINUTES', 5)
    now = timezone.now().strftime('%Y-%m-%d %H:%M UTC')
    return (
        f"Component: {component}\n"
        f"Status: DOWN\n"
        f"Consecutive failed checks: {failures} (checked every {interval} minutes)\n"
        f"Detected at: {now}\n"
        f"Last error: {error}\n"
    )


@shared_task(queue='scheduling_queue')
def infrastructure_health_check():
    # Emails go through team_alerts.alerting (KAZ_AND_TEAM): first occurrence,
    # reminder after ALERT_REMIND_HOURS, [RESOLVED] on recovery.
    from .alerting import raise_alert, resolve_alert

    threshold = int(getattr(settings, 'HEALTHCHECK_FAILURES_BEFORE_ALERT', 2))
    state = _load_state()
    summary = {}

    for component in COMPONENTS:
        entry = state.get(component) or {}
        failures = int(entry.get('failures') or 0)

        try:
            CHECKS[component]()
            ok, error = True, None
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"

        if ok:
            if failures:
                logger.info(f"infra_health_check: {component} recovered after {failures} failed check(s).")
                resolve_alert(f'infra:{component}', note=f"{component} answered again after {failures} failed check(s).")
            state[component] = {'failures': 0, 'last_ok': timezone.now().isoformat()}
            summary[component] = 'ok'
            continue

        failures += 1
        logger.error(f"infra_health_check: {component} check FAILED ({failures} in a row): {error}")
        if failures >= threshold:
            raise_alert(
                f'infra:{component}', 'team',
                f"[ALERT] Relister {component} is DOWN — {failures} consecutive checks failed",
                _alert_body(component, failures, error),
            )
        state[component] = {
            'failures': failures,
            'last_error': error,
            'last_failed': timezone.now().isoformat(),
        }
        summary[component] = f'down ({failures})'

    _save_state(state)
    logger.info(f"infra_health_check: {summary}")
    return summary
