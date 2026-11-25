"""
ZenRows credit monitoring — usage thresholds, run-out projection and 402 alerts.

All emails go to KAZ_AND_TEAM through team_alerts.alerting (first occurrence,
reminder after ALERT_REMIND_HOURS, escalation when a level changes, [RESOLVED]
when the condition clears).

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

   Alerts:
     - 'zenrows:usage'      WARNING at ZENROWS_MONITOR_WARN_PCT (80%),
                            CRITICAL at ZENROWS_MONITOR_CRIT_PCT (95%);
                            resolved when usage is back under 80% (new period).
     - 'zenrows:projection' at the current average rate (usage so far ÷ time
                            elapsed in the period) credits reach 100% before
                            period_ends_at. Evaluated once at least
                            ZENROWS_MONITOR_PROJECTION_MIN_ELAPSED_PCT (10%)
                            of the period has elapsed; resolved when on track.

2. 402 HOOK (installed by team_alerts/apps.py at startup)
   `ZenRowsClient.get` is wrapped so every scraper response passes through
   `report_zenrows_response()`. A 402 (Payment Required / out of credits)
   raises 'zenrows:402' immediately; the central sender suppresses repeats
   (one email, then a reminder after ALERT_REMIND_HOURS). The first non-402
   response seen afterwards resolves it. Responses are returned untouched.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .alerting import raise_alert, resolve_alert

logger = logging.getLogger('relister_views')

USAGE_URL_DEFAULT = 'https://api.zenrows.com/v1/subscriptions/self/details'


def _setting(name, default):
    return getattr(settings, name, default)


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
    will_run_out, projected_end_pct, runout_at = project_runout(percent, usage['period_start'], period_end, now)

    summary_lines = [
        f"Plan: {usage['plan']}  (status: {usage['status']})",
        f"Credits used this period: {percent:.1f}%",
        f"Billing period: {_fmt(usage['period_start'])} → {_fmt(period_end)}",
    ]
    if projected_end_pct is not None:
        summary_lines.append(f"Projected usage by renewal at the current rate: {projected_end_pct:.0f}%")
    if runout_at is not None:
        summary_lines.append(f"Estimated run-out at the current rate: {_fmt(runout_at)}")

    alerts = {}
    if level == 'ok':
        resolve_alert('zenrows:usage', note=f"ZenRows usage is {percent:.1f}% of the plan limit.")
    else:
        tag = 'CRITICAL' if level == 'critical' else 'WARNING'
        alerts['usage'] = raise_alert(
            'zenrows:usage', 'team',
            f"[{tag}] ZenRows credits at {percent:.0f}% of the monthly limit",
            '\n'.join([f"ZenRows credit usage has reached {percent:.1f}% of the plan limit.", ''] + summary_lines + [
                '',
                'Gumtree, custom-domain and image scraping stop working once credits are exhausted (ZenRows returns 402).',
                'Consider a top-up or plan upgrade at https://app.zenrows.com/.',
                f"Monitoring is now running every {_setting('ZENROWS_MONITOR_ELEVATED_INTERVAL_HOURS', 3)} hours until the period renews.",
            ]) + '\n',
            severity=level,
        )

    if will_run_out and level != 'critical':
        alerts['projection'] = raise_alert(
            'zenrows:projection', 'team',
            f"[WARNING] ZenRows credits projected to run out {_fmt(runout_at)} — before renewal {_fmt(period_end)}",
            '\n'.join(['At the current usage rate the ZenRows credits will be exhausted before the billing period renews.', '']
                      + summary_lines + ['', 'Either reduce scraping volume or arrange a top-up/upgrade before the estimated run-out date.']) + '\n',
        )
    elif will_run_out is False or level == 'critical':
        resolve_alert('zenrows:projection', note=f"Projected usage by renewal is now {projected_end_pct:.0f}%." if projected_end_pct is not None else None)

    state.update({
        'last_checked_at': now.isoformat(),
        'last_percent': percent,
        'last_level': level,
        'last_error': None,
        'period_start': usage['period_start'].isoformat() if usage['period_start'] else None,
        'period_end': period_end.isoformat() if period_end else None,
        'projected_end_pct': projected_end_pct,
        'runout_at': runout_at.isoformat() if runout_at else None,
    })
    _save_state(state)
    logger.info(f"zenrows_monitor: usage {percent:.1f}% level={level} projected_end={projected_end_pct} alerts={alerts}")
    return {'checked': True, 'percent': percent, 'level': level, 'will_run_out': will_run_out, 'alerts': alerts}


# ── 402 hook ────────────────────────────────────────────────────────────────

# Set in the process that saw a 402 so its next successful response resolves the alert.
_saw_402 = False


def report_zenrows_response(response, url=None):
    """Called for every ZenRows response. 402 raises the alert; the next non-402 resolves it."""
    global _saw_402
    status = getattr(response, 'status_code', None)
    if status != 402:
        if _saw_402 and status is not None:
            _saw_402 = False
            resolve_alert('zenrows:402', note=f"ZenRows answered HTTP {status} again for {url or 'a scraper request'}.")
        return False
    _saw_402 = True
    target = url or ''
    logger.error(f"zenrows_monitor: ZenRows returned 402 Payment Required (out of credits) for {target}")
    detail = ''
    try:
        detail = (response.text or '')[:300]
    except Exception:
        pass
    state = _load_state()
    result = raise_alert(
        'zenrows:402', 'team',
        '[CRITICAL] ZenRows 402 — out of credits, scraping is failing',
        '\n'.join([
            'ZenRows answered a scraper request with HTTP 402 Payment Required — the account is OUT OF CREDITS',
            '(or the API key is invalid). Gumtree / custom-domain / image scraping is failing right now.',
            '',
            f"Requested page: {target or 'unknown'}",
            f"ZenRows response: {detail or '(empty)'}",
            f"Last known usage: {state.get('last_percent', 'unknown')}% at {state.get('last_checked_at', 'never')}",
            '',
            'Top up or upgrade the plan at https://app.zenrows.com/ and check the API key.',
        ]) + '\n',
    )
    return result in ('sent', 'reminded', 'escalated')


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
