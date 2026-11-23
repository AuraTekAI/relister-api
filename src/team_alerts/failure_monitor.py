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
busy kind needs FACTOR× its usual rate). Emails go to KAZ_ONLY through
team_alerts.alerting: one when the spike starts, a reminder after
ALERT_REMIND_HOURS if it persists, [RESOLVED] when the rate is normal again.
"""
import logging
import re
from collections import Counter
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from .alerting import raise_alert, recipients, resolve_alert

logger = logging.getLogger('relister_views')

KINDS = ('publish', 'delete', 'form_fill')
KIND_LABELS = {'publish': 'publish', 'delete': 'delete', 'form_fill': 'Facebook form-fill'}

# Reasons produced by the extension's form-fill failure describer
# (publishListing.ts describeFillFailure) all mention Facebook's form.
_FORM_FILL_RE = re.compile(r"facebook'?s? form|form[ -]fill|empty required field|not in facebook'?s? list", re.I)


def _setting(name, default):
    return getattr(settings, name, default)


def kaz_only():
    return recipients('kaz')


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


def _spike_email(kind, stats):
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
        f"Checked at: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    return subject, '\n'.join(lines) + '\n'


@shared_task(queue='scheduling_queue')
def failure_spike_monitor():
    now = timezone.now()
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
        key = f'failures:{kind}'
        if not spike:
            resolve_alert(key, note=f"{stats['current']} {KIND_LABELS[kind]} failures in the last {stats['window_minutes']} minutes (normal ≈ {stats['baseline']:.1f}).")
            continue
        logger.error(f"failure_monitor: {kind} failure SPIKE — {stats['current']} in {stats['window_minutes']}m (normal {stats['baseline']:.1f}).")
        subject, body = _spike_email(kind, stats)
        raise_alert(key, 'kaz', subject, body)
    logger.info(f"failure_monitor: {summary}")
    return summary
