"""
Infrastructure health check — runs every 5 minutes via Celery Beat
(registered in VehicleListing/apps.py as "Infrastructure_Health_Check").

Checks that the database accepts a query and that Redis answers PING. Each
component keeps its own consecutive-failure count in a small JSON state file
(NOT in the DB or Redis — those are exactly what may be down). After
HEALTHCHECK_FAILURES_BEFORE_ALERT failures in a row (default 2, i.e. ~10 min of
outage) ONE alert email is sent to HEALTHCHECK_ALERT_EMAIL through the existing
Flashpost email backend. The count resets as soon as the component recovers.

The email is sent with fail_silently=False so a Flashpost rejection is logged
and the alert is retried on the next run instead of being silently lost.
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

COMPONENTS = ('database', 'redis')


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


def _send_alert(component, failures, error):
    """Send the alert through the existing Flashpost backend. Returns True on delivery."""
    to_email = getattr(settings, 'HEALTHCHECK_ALERT_EMAIL', 'kazp@citysubaru.com.au')
    interval = getattr(settings, 'HEALTHCHECK_INTERVAL_MINUTES', 5)
    now = timezone.now().strftime('%Y-%m-%d %H:%M UTC')
    subject = f"[ALERT] Relister {component} is DOWN — {failures} consecutive checks failed"
    body = (
        f"Component: {component}\n"
        f"Status: DOWN\n"
        f"Consecutive failed checks: {failures} (checked every {interval} minutes)\n"
        f"Detected at: {now}\n"
        f"Last error: {error}\n\n"
        "This alert is sent once per outage. The failure counter resets when the component recovers.\n"
    )
    try:
        sent = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
        ).send(fail_silently=False)
        if sent != 1:
            logger.error(f"infra_health_check: Flashpost reported {sent} sent for {component} alert — will retry next run.")
            return False
        logger.info(f"infra_health_check: alert for {component} sent to {to_email}.")
        return True
    except Exception as exc:
        logger.error(f"infra_health_check: FAILED to send {component} alert to {to_email}: {exc} — will retry next run.")
        return False


@shared_task(queue='scheduling_queue')
def infrastructure_health_check():
    threshold = int(getattr(settings, 'HEALTHCHECK_FAILURES_BEFORE_ALERT', 2))
    state = _load_state()
    summary = {}

    for component in COMPONENTS:
        entry = state.get(component) or {}
        failures = int(entry.get('failures') or 0)
        alerted = bool(entry.get('alerted'))

        try:
            CHECKS[component]()
            ok, error = True, None
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"

        if ok:
            if failures:
                logger.info(f"infra_health_check: {component} recovered after {failures} failed check(s).")
            state[component] = {'failures': 0, 'alerted': False, 'last_ok': timezone.now().isoformat()}
            summary[component] = 'ok'
            continue

        failures += 1
        logger.error(f"infra_health_check: {component} check FAILED ({failures} in a row): {error}")
        if failures >= threshold and not alerted:
            alerted = _send_alert(component, failures, error)
        state[component] = {
            'failures': failures,
            'alerted': alerted,
            'last_error': error,
            'last_failed': timezone.now().isoformat(),
        }
        summary[component] = f'down ({failures})'

    _save_state(state)
    logger.info(f"infra_health_check: {summary}")
    return summary
