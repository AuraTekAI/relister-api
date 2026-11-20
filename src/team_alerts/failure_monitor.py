"""
Failure-spike monitoring — publish, delete and Facebook form-fill failures.

Every failed attempt is one FailureEvent row (team_alerts.models):
  - publish / form_fill: written by /api/vehicle-listing/publish-failures/
    (the server-side failure record) via record_failure_event(); the kind is
    the extension's explicit `kind` or, failing that, classified from the
    reason text (form-fill reasons all mention Facebook's form).
  - delete: reported by the extension via /api/team-alerts/failure-events/.

Beat task "Failure_Spike_Monitor" (every FAILURE_SPIKE_CHECK_MINUTES) compares,
per kind, the failures in the last FAILURE_SPIKE_WINDOW_MINUTES against the
normal rate: the average per-window count over the previous
FAILURE_SPIKE_BASELINE_DAYS. It is a SPIKE when

    current >= FAILURE_SPIKE_MIN_COUNT  and  current >= FAILURE_SPIKE_FACTOR × baseline

(so a brand-new kind with no history still needs MIN_COUNT failures, and a
busy kind needs FACTOR× its usual rate). One email goes to KAZ_ONLY per spike
episode; the kind re-arms once a check finds it back under the line.
"""
import json
import logging
import os
import re
import tempfile
from collections import Counter
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger('relister_views')

KINDS = ('publish', 'delete', 'form_fill')
KIND_LABELS = {'publish': 'publish', 'delete': 'delete', 'form_fill': 'Facebook form-fill'}

# Reasons produced by the extension's form-fill failure describer
# (publishListing.ts describeFillFailure) all mention Facebook's form.
_FORM_FILL_RE = re.compile(r"facebook'?s? form|form[ -]fill|empty required field|not in facebook'?s? list", re.I)


def _setting(name, default):
    return getattr(settings, name, default)


def kaz_only():
    raw = _setting('KAZ_ONLY', ['kazp@citysubaru.com.au'])
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(',')]
    return [r for r in raw if r]


def classify_publish_failure_kind(reason, explicit_kind=None):
    if explicit_kind in KINDS:
        return explicit_kind
    if reason and _FORM_FILL_RE.search(str(reason)):
        return 'form_fill'
    return 'publish'


def record_failure_event(user, kind, listing_id=None, reason=None, occurred_at=None):
    """Best-effort timeline write — never raises into the caller's request."""
    from .models import FailureEvent
    if kind not in KINDS:
        kind = 'publish'
    try:
        return FailureEvent.objects.create(
            user=user, kind=kind, listing_id=listing_id,
            reason=(str(reason)[:2000] if reason else None),
            occurred_at=occurred_at or timezone.now(),
        )
    except Exception as exc:
        logger.error(f"failure_monitor: could not record {kind} failure event: {exc}")
        return None


# ── state ──────────────────────────────────────────────────────────────────

def _state_file():
    return _setting('FAILURE_SPIKE_STATE_FILE', os.path.join(tempfile.gettempdir(), 'relister_failure_spike.json'))


def _load_state():
    try:
        with open(_state_file(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"failure_monitor: could not read state file: {exc}")
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
        logger.error(f"failure_monitor: could not write state file {path}: {exc}")


# ── detection ──────────────────────────────────────────────────────────────

def is_spike(current, baseline_per_window):
    min_count = int(_setting('FAILURE_SPIKE_MIN_COUNT', 5))
    factor = float(_setting('FAILURE_SPIKE_FACTOR', 3.0))
    return current >= min_count and current >= factor * baseline_per_window


def _window_stats(kind, now):
    from .models import FailureEvent
    window = timedelta(minutes=int(_setting('FAILURE_SPIKE_WINDOW_MINUTES', 60)))
    baseline_days = int(_setting('FAILURE_SPIKE_BASELINE_DAYS', 7))
    window_start = now - window
    baseline_start = window_start - timedelta(days=baseline_days)

    recent = FailureEvent.objects.filter(kind=kind, occurred_at__gt=window_start, occurred_at__lte=now)
    current = recent.count()
    baseline_total = FailureEvent.objects.filter(kind=kind, occurred_at__gt=baseline_start, occurred_at__lte=window_start).count()
    windows_in_baseline = (timedelta(days=baseline_days) / window) or 1
    baseline = baseline_total / windows_in_baseline

    dealers = list(
        recent.values('user_id', 'user__email', 'user__dealership_name')
        .annotate(n=Count('id')).order_by('-n')[:10]
    )
    reasons = Counter((r or 'no reason given')[:120] for r in recent.values_list('reason', flat=True))
    return {
        'current': current, 'baseline': baseline, 'baseline_total': baseline_total,
        'window_minutes': int(window.total_seconds() // 60), 'baseline_days': baseline_days,
        'dealers': dealers, 'top_reasons': reasons.most_common(5),
    }


def _send_spike_alert(kind, stats):
    to = kaz_only()
    label = KIND_LABELS[kind]
    subject = f"[ALERT] Spike in {label} failures: {stats['current']} in the last {stats['window_minutes']} min (normal ≈ {stats['baseline']:.1f})"
    lines = [
        f"{stats['current']} {label} failures were recorded in the last {stats['window_minutes']} minutes.",
        f"Normal for this window is about {stats['baseline']:.1f} (average over the previous {stats['baseline_days']} days, {stats['baseline_total']} total).",
        '',
        'Dealers affected (most failures first):',
    ]
    for d in stats['dealers']:
        name = d.get('user__dealership_name') or d.get('user__email') or f"user {d['user_id']}"
        lines.append(f"  - {name} (id {d['user_id']}): {d['n']}")
    if stats['top_reasons']:
        lines += ['', 'Most common reasons:']
        for reason, n in stats['top_reasons']:
            lines.append(f"  - {n}× {reason}")
    lines += [
        '',
        'A spike across several dealers usually means Facebook changed something or is blocking; a single dealer usually means that account.',
        'This is sent once per spike; it re-arms when the rate returns to normal.',
        f"Checked at: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    try:
        sent = EmailMessage(subject=subject, body='\n'.join(lines) + '\n', from_email=settings.DEFAULT_FROM_EMAIL, to=to).send(fail_silently=False)
        if sent != 1:
            logger.error(f"failure_monitor: Flashpost reported {sent} sent for '{subject}' — will retry next run.")
            return False
        logger.info(f"failure_monitor: spike alert sent to {to}: {subject}")
        return True
    except Exception as exc:
        logger.error(f"failure_monitor: FAILED to send spike alert to {to}: {exc} — will retry next run.")
        return False


@shared_task(queue='scheduling_queue')
def failure_spike_monitor():
    now = timezone.now()
    state = _load_state()
    alerted = state.setdefault('alerted', {})
    summary = {}
    for kind in KINDS:
        try:
            stats = _window_stats(kind, now)
        except Exception as exc:
            logger.error(f"failure_monitor: could not compute stats for {kind}: {exc}")
            summary[kind] = 'error'
            continue
        spike = is_spike(stats['current'], stats['baseline'])
        summary[kind] = f"{'SPIKE' if spike else 'ok'} {stats['current']} vs {stats['baseline']:.1f}"
        if not spike:
            if alerted.get(kind):
                logger.info(f"failure_monitor: {kind} failures back to normal — alert re-armed.")
            alerted[kind] = False
            continue
        if alerted.get(kind):
            logger.warning(f"failure_monitor: {kind} spike continues ({stats['current']} in window) — already alerted.")
            continue
        logger.error(f"failure_monitor: {kind} failure SPIKE — {stats['current']} in {stats['window_minutes']}m (normal {stats['baseline']:.1f}).")
        if _send_spike_alert(kind, stats):
            alerted[kind] = True
    state['checked_at'] = now.isoformat()
    _save_state(state)
    logger.info(f"failure_monitor: {summary}")
    return summary
