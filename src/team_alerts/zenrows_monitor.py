"""
ZenRows credit monitoring — usage thresholds, run-out projection and 402 alerts.

Two independent pieces, both alerting KAZ_AND_TEAM through the existing
Flashpost email backend (fail_silently=False → a rejected send is logged and
retried on the next run instead of being lost):

1. USAGE MONITOR (Beat task "ZenRows_Usage_Monitor")
   Calls ZenRows' subscription endpoint
       GET https://api.zenrows.com/v1/subscriptions/self/details   (X-API-Key)
   which returns usage_percent (0-100) and the billing period
   (period_starts_at / period_ends_at, ISO-8601 UTC).

   Frequency: the Beat row fires every ZENROWS_MONITOR_ELEVATED_INTERVAL_HOURS
   (default 3 h), but the task only *calls* ZenRows when a check is due:
     - usage below ZENROWS_MONITOR_ELEVATED_FROM_PCT (80%): once every
       ZENROWS_MONITOR_INTERVAL_HOURS (24 h → daily);
     - usage at/above 80%: every run (every 3 h).
   So escalation happens automatically with no Beat schedule rewrite, and
   the daily cadence costs one tiny request per day.

   Alerts (each sent ONCE per billing period, re-armed when the period rolls
   over or usage drops back below the level):
     - [WARNING]  usage >= ZENROWS_MONITOR_WARN_PCT  (80%)
     - [CRITICAL] usage >= ZENROWS_MONITOR_CRIT_PCT  (95%)
     - [WARNING]  run-out projection: at the current average rate
       (usage so far ÷ time elapsed in the period) credits reach 100% before
       period_ends_at. Only evaluated once at least
       ZENROWS_MONITOR_PROJECTION_MIN_ELAPSED_PCT (10%) of the period has
       elapsed, so day-one spikes do not cry wolf.

2. 402 HOOK (installed by team_alerts/apps.py at startup)
   `ZenRowsClient.get` is wrapped so every scraper response passes through
   `report_zenrows_response()`. A 402 (Payment Required / out of credits)
   triggers an IMMEDIATE email. The response is returned untouched, so no
   scraper logic changes. To avoid one email per vehicle during a dealer
   sync, repeats are suppressed for ZENROWS_402_ALERT_COOLDOWN_MINUTES
   (default 60) — the suppression key lives in the Redis cache so web and
   worker processes share it, falling back to the state file.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

logger = logging.getLogger('relister_views')

USAGE_URL_DEFAULT = 'https://api.zenrows.com/v1/subscriptions/self/details'
LEVEL_RANK = {'ok': 0, 'warning': 1, 'critical': 2}


def _setting(name, default):
    return getattr(settings, name, default)


def _recipients():
    raw = _setting('KAZ_AND_TEAM', ['kazp@citysubaru.com.au'])
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(',')]
    return [r for r in raw if r]


def _state_file():
    return _setting('ZENROWS_MONITOR_STATE_FILE', os.path.join(tempfile.gettempdir(), 'relister_zenrows_monitor.json'))


def _load_state():
    try:
        with open(_state_file(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"zenrows_monitor: could not read state file: {exc}")
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
        logger.error(f"zenrows_monitor: could not write state file {path}: {exc}")


def _send_alert(subject, lines):
    to = _recipients()
    body = '\n'.join(lines) + f"\n\nChecked at: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}\n"
    try:
        sent = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=to).send(fail_silently=False)
        if sent != 1:
            logger.error(f"zenrows_monitor: Flashpost reported {sent} sent for '{subject}' — will retry.")
            return False
        logger.info(f"zenrows_monitor: alert sent to {to}: {subject}")
        return True
    except Exception as exc:
        logger.error(f"zenrows_monitor: FAILED to send '{subject}' to {to}: {exc} — will retry.")
        return False


# ── Usage API ───────────────────────────────────────────────────────────────

def _parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=dt_timezone.utc)


def fetch_zenrows_usage():
    """
    Returns a dict {percent, period_start, period_end, plan, status, raw} or
    raises on HTTP/network/parse failure. Uses the key from settings; the key
    is never logged.
    """
    api_key = _setting('ZENROWS_API_KEY', None)
    if not api_key:
        raise RuntimeError('ZENROWS_API_KEY is not configured')
    resp = requests.get(_setting('ZENROWS_USAGE_URL', USAGE_URL_DEFAULT), headers={'X-API-Key': api_key}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"usage endpoint HTTP {resp.status_code}")
    data = resp.json()
    percent = data.get('usage_percent')
    if percent is None and data.get('credit_limit'):
        percent = 100.0 * float(data.get('usage_credits') or 0) / float(data['credit_limit'])
    if percent is None:
        raise RuntimeError('usage endpoint response has no usage_percent')
    plan = data.get('plan') or {}
    return {
        'percent': float(percent),
        'period_start': _parse_iso(data.get('period_starts_at')),
        'period_end': _parse_iso(data.get('period_ends_at')),
        'plan': plan.get('name') or 'unknown',
        'status': data.get('status'),
        'raw': data,
    }


def usage_level(percent):
    if percent >= float(_setting('ZENROWS_MONITOR_CRIT_PCT', 95)):
        return 'critical'
    if percent >= float(_setting('ZENROWS_MONITOR_WARN_PCT', 80)):
        return 'warning'
    return 'ok'


def project_runout(percent, period_start, period_end, now):
    """
    Linear projection from the usage so far in this billing period.
    Returns (will_run_out_before_renewal, projected_percent_at_period_end,
    estimated_runout_datetime). All None when there is not enough data
    (unknown period, or less than ZENROWS_MONITOR_PROJECTION_MIN_ELAPSED_PCT
    of the period elapsed).
    """
    if not period_start or not period_end or period_end <= period_start:
        return None, None, None
    total = (period_end - period_start).total_seconds()
    elapsed = (now - period_start).total_seconds()
    min_elapsed = float(_setting('ZENROWS_MONITOR_PROJECTION_MIN_ELAPSED_PCT', 10)) / 100.0
    if elapsed <= 0 or elapsed / total < min_elapsed:
        return None, None, None
    rate_per_sec = percent / elapsed
    projected_end = rate_per_sec * total
    if rate_per_sec <= 0:
        return False, projected_end, None
    runout = period_start + timedelta(seconds=(100.0 / rate_per_sec))
    return runout < period_end, projected_end, runout


def _fmt(dt):
    return dt.strftime('%Y-%m-%d %H:%M UTC') if dt else 'unknown'


def _check_due(state, now):
    """Daily normally; every run (elevated cadence) once usage ≥ ELEVATED_FROM_PCT."""
    last = _parse_iso(state.get('last_checked_at'))
    if last is None:
        return True
    last_pct = float(state.get('last_percent') or 0)
    if last_pct >= float(_setting('ZENROWS_MONITOR_ELEVATED_FROM_PCT', 80)):
        return True
    daily = timedelta(hours=float(_setting('ZENROWS_MONITOR_INTERVAL_HOURS', 24)))
    # 5-minute slack so a Beat tick a few seconds early still counts as "a day later".
    return now - last >= daily - timedelta(minutes=5)


@shared_task(queue='scheduling_queue')
def zenrows_usage_monitor(force=False):
    now = timezone.now()
    state = _load_state()
    if not force and not _check_due(state, now):
        logger.info("zenrows_monitor: not due yet (usage under elevated threshold; daily cadence) — skipped.")
        return {'checked': False, 'reason': 'not_due'}

    try:
        usage = fetch_zenrows_usage()
    except Exception as exc:
        logger.error(f"zenrows_monitor: could not read ZenRows usage: {exc}")
        state['last_error'] = str(exc)
        state['last_error_at'] = now.isoformat()
        _save_state(state)
        return {'checked': False, 'reason': f'error: {exc}'}

    percent = usage['percent']
    level = usage_level(percent)
    period_end = usage['period_end']
    period_key = period_end.isoformat() if period_end else None

    # New billing period → everything re-arms.
    if state.get('period_key') != period_key:
        state.update({'period_key': period_key, 'alerted_level': 'ok', 'projection_alerted': False})
    # Usage fell back below the level we alerted at → re-arm that level.
    if LEVEL_RANK[level] < LEVEL_RANK.get(state.get('alerted_level', 'ok'), 0):
        state['alerted_level'] = level

    will_run_out, projected_end_pct, runout_at = project_runout(percent, usage['period_start'], period_end, now)
    if will_run_out is False:
        state['projection_alerted'] = False

    summary_lines = [
        f"Plan: {usage['plan']}  (status: {usage['status']})",
        f"Credits used this period: {percent:.1f}%",
        f"Billing period: {_fmt(usage['period_start'])} → {_fmt(period_end)}",
    ]
    if projected_end_pct is not None:
        summary_lines.append(f"Projected usage by renewal at the current rate: {projected_end_pct:.0f}%")
    if runout_at is not None:
        summary_lines.append(f"Estimated run-out at the current rate: {_fmt(runout_at)}")

    sent = []
    # Threshold alerts — once per level per period.
    if LEVEL_RANK[level] > LEVEL_RANK.get(state.get('alerted_level', 'ok'), 0):
        tag = 'CRITICAL' if level == 'critical' else 'WARNING'
        subject = f"[{tag}] ZenRows credits at {percent:.0f}% of the monthly limit"
        lines = [f"ZenRows credit usage has reached {percent:.1f}% of the plan limit.", ''] + summary_lines + [
            '',
            'Gumtree, custom-domain and image scraping stop working once credits are exhausted (ZenRows returns 402).',
            'Consider a top-up or plan upgrade at https://app.zenrows.com/.',
            f"Monitoring is now running every {_setting('ZENROWS_MONITOR_ELEVATED_INTERVAL_HOURS', 3)} hours until the period renews.",
        ]
        if _send_alert(subject, lines):
            state['alerted_level'] = level
            sent.append(level)

    # Projection alert — once per period, re-armed if the rate drops back under.
    if will_run_out and not state.get('projection_alerted') and level != 'critical':
        subject = f"[WARNING] ZenRows credits projected to run out {_fmt(runout_at)} — before renewal {_fmt(period_end)}"
        lines = [
            'At the current usage rate the ZenRows credits will be exhausted before the billing period renews.',
            '',
        ] + summary_lines + [
            '',
            'Either reduce scraping volume or arrange a top-up/upgrade before the estimated run-out date.',
        ]
        if _send_alert(subject, lines):
            state['projection_alerted'] = True
            sent.append('projection')

    state.update({
        'last_checked_at': now.isoformat(),
        'last_percent': percent,
        'last_level': level,
        'last_error': None,
        'period_start': usage['period_start'].isoformat() if usage['period_start'] else None,
        'projected_end_pct': projected_end_pct,
        'runout_at': runout_at.isoformat() if runout_at else None,
    })
    _save_state(state)
    logger.info(f"zenrows_monitor: usage {percent:.1f}% level={level} projected_end={projected_end_pct} alerts={sent}")
    return {'checked': True, 'percent': percent, 'level': level, 'will_run_out': will_run_out, 'alerts': sent}


# ── 402 hook ────────────────────────────────────────────────────────────────

def _acquire_402_slot(now):
    """True if a 402 alert may be sent now (first 402, or cooldown expired).
    Shared across processes via the Redis cache; state file as fallback."""
    cooldown_min = int(_setting('ZENROWS_402_ALERT_COOLDOWN_MINUTES', 60))
    try:
        from django.core.cache import cache
        return bool(cache.add('zenrows_monitor:402_alerted', now.isoformat(), timeout=cooldown_min * 60))
    except Exception as exc:
        logger.warning(f"zenrows_monitor: cache unavailable for 402 throttle ({exc}); using state file.")
    state = _load_state()
    last = _parse_iso(state.get('last_402_alert_at'))
    if last and now - last < timedelta(minutes=cooldown_min):
        return False
    state['last_402_alert_at'] = now.isoformat()
    _save_state(state)
    return True


def report_zenrows_response(response, url=None):
    """Called for every ZenRows response. Only a 402 does anything."""
    if getattr(response, 'status_code', None) != 402:
        return False
    now = timezone.now()
    target = url or ''
    logger.error(f"zenrows_monitor: ZenRows returned 402 Payment Required (out of credits) for {target}")
    if not _acquire_402_slot(now):
        logger.warning("zenrows_monitor: 402 alert suppressed — one was sent within the cooldown window.")
        return False
    detail = ''
    try:
        detail = (response.text or '')[:300]
    except Exception:
        pass
    state = _load_state()
    lines = [
        'ZenRows answered a scraper request with HTTP 402 Payment Required — the account is OUT OF CREDITS',
        '(or the API key is invalid). Gumtree / custom-domain / image scraping is failing right now.',
        '',
        f"Requested page: {target or 'unknown'}",
        f"ZenRows response: {detail or '(empty)'}",
        f"Last known usage: {state.get('last_percent', 'unknown')}% at {state.get('last_checked_at', 'never')}",
        '',
        'Top up or upgrade the plan at https://app.zenrows.com/ and check the API key.',
        f"Further 402s are suppressed for {_setting('ZENROWS_402_ALERT_COOLDOWN_MINUTES', 60)} minutes.",
    ]
    ok = _send_alert('[CRITICAL] ZenRows 402 — out of credits, scraping is failing', lines)
    state['last_402_at'] = now.isoformat()
    if ok:
        state['last_402_alert_at'] = now.isoformat()
    _save_state(state)
    return ok


def install_zenrows_402_hook():
    """Wrap ZenRowsClient.get so every response is inspected. Idempotent."""
    try:
        from zenrows import ZenRowsClient
    except Exception as exc:  # pragma: no cover
        logger.warning(f"zenrows_monitor: zenrows SDK not importable, 402 hook not installed: {exc}")
        return False
    if getattr(ZenRowsClient.get, '_relister_402_hook', False):
        return True
    original_get = ZenRowsClient.get

    def get(self, url, *args, **kwargs):
        response = original_get(self, url, *args, **kwargs)
        try:
            report_zenrows_response(response, url)
        except Exception as exc:
            logger.error(f"zenrows_monitor: 402 hook error (ignored): {exc}")
        return response

    get._relister_402_hook = True
    get._relister_original = original_get
    ZenRowsClient.get = get
    return True
