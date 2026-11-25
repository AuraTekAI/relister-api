"""
Per-dealer Facebook account issue reporting — restricted / checkpoint /
logged out — and the Kaz-only alert for each.

POST /api/team-alerts/fb-account-events/     (extension, authenticated dealer)
    { "issue_type": "restricted" | "checkpoint" | "logged_out",
      "status": "detected" | "resolved",            (default "detected")
      "detected_at": "<ISO-8601 exact browser time>",
      "detail": "...", "fb_user_id": "...", "extension_version": "1.2.56",
      "dealer_id": 123 }                            (must match the caller)

Every report is stored as a FacebookAccountEvent (dealer, issue type, exact
timestamp). A `detected` report raises the alert 'fb:<issue>:<dealer id>'
through team_alerts.alerting (KAZ_ONLY): the first report emails Kaz, repeats
while the issue is open are suppressed (a reminder goes out only after
ALERT_REMIND_HOURS), and a `resolved` report sends the [RESOLVED] email.
`alerted` on the stored event is True only when an email was actually sent.

POST /api/team-alerts/failure-events/         (extension)
    { "kind": "delete" | "publish" | "form_fill", "listing_id": 123,
      "reason": "...", "occurred_at": "<ISO-8601>" }
Stores one FailureEvent row for the spike monitor (team_alerts/failure_monitor.py).
"""
import logging
from datetime import datetime, timezone as dt_timezone

from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from .alerting import raise_alert, resolve_alert
from .failure_monitor import KINDS, record_failure_event
from .models import FacebookAccountEvent

logger = logging.getLogger('relister_views')

ISSUE_TYPES = {c[0] for c in FacebookAccountEvent.ISSUE_CHOICES}
ISSUE_TITLES = {
    'restricted': 'Facebook account restricted',
    'checkpoint': 'Facebook security checkpoint',
    'logged_out': 'Logged out of Facebook',
}
ISSUE_EXPLANATIONS = {
    'restricted': 'The extension can reach Facebook but Marketplace shows none of this dealer\'s published listings — the account is likely restricted or locked. Publishing is paused.',
    'checkpoint': 'Facebook redirected the dealer to a security check / verification page. Publishing, photo uploads and relisting are stopped until the dealer completes it.',
    'logged_out': 'The browser no longer has a Facebook login (session cookies missing). Nothing can be published or relisted until the dealer logs in again.',
}


def _parse_dt(value):
    if value in (None, ''):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.replace('.', '', 1).isdigit()):
            secs = float(value)
            if secs > 1e11:  # milliseconds (JS Date.now())
                secs /= 1000.0
            return datetime.fromtimestamp(secs, tz=dt_timezone.utc)
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=dt_timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _alert_key(event):
    return f"fb:{event.issue_type}:{event.user_id}"


def _alert_email(event):
    user = event.user
    dealer = getattr(user, 'dealership_name', None) or user.email
    title = ISSUE_TITLES[event.issue_type]
    subject = f"[ALERT] {title} — {dealer}"
    body = '\n'.join([
        f"{title} for dealer {dealer}.",
        '',
        f"Dealer: {dealer}  (id {user.id}, {user.email})",
        f"Issue: {event.issue_type}",
        f"Detected at: {event.detected_at.strftime('%Y-%m-%d %H:%M:%S UTC')} (extension time)",
        f"Facebook account: {event.fb_user_id or 'unknown'}",
        f"Extension version: {event.extension_version or 'unknown'}",
        f"Detail: {event.detail or '-'}",
        '',
        ISSUE_EXPLANATIONS[event.issue_type],
    ]) + '\n'
    return subject, body


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def facebook_account_event(request):
    data = request.data if isinstance(request.data, dict) else {}
    issue_type = data.get('issue_type')
    if issue_type not in ISSUE_TYPES:
        return JsonResponse({'success': False, 'error': f"issue_type must be one of {sorted(ISSUE_TYPES)}"}, status=400)
    status = data.get('status', 'detected')
    if status not in ('detected', 'resolved'):
        return JsonResponse({'success': False, 'error': 'status must be "detected" or "resolved"'}, status=400)
    dealer_id = data.get('dealer_id')
    if dealer_id not in (None, '') and str(dealer_id) != str(request.user.id):
        return JsonResponse({'success': False, 'error': 'dealer_id does not match the authenticated dealer'}, status=400)

    now = timezone.now()
    detected_at = _parse_dt(data.get('detected_at')) or now
    event = FacebookAccountEvent.objects.create(
        user=request.user, issue_type=issue_type, status=status,
        detail=(str(data.get('detail') or '')[:500] or None),
        fb_user_id=(str(data.get('fb_user_id') or '')[:40] or None),
        extension_version=(str(data.get('extension_version') or '')[:32] or None),
        detected_at=detected_at,
    )
    logger.warning(f"fb-account-events: user={request.user.id} {issue_type} {status} at {detected_at.isoformat()} detail={event.detail}")

    alerted = False
    suppressed = False
    if status == 'detected':
        subject, body = _alert_email(event)
        result = raise_alert(_alert_key(event), 'kaz', subject, body)
        alerted = result in ('sent', 'reminded', 'escalated')
        suppressed = result == 'suppressed'
        if alerted:
            event.alerted = True
            event.save(update_fields=['alerted'])
    else:
        resolve_alert(_alert_key(event), note=f"The extension reported the issue resolved at {detected_at.strftime('%Y-%m-%d %H:%M:%S UTC')}: {event.detail or '-'}")
    return JsonResponse({'success': True, 'event_id': event.id, 'alerted': alerted, 'suppressed': suppressed}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def failure_event(request):
    data = request.data if isinstance(request.data, dict) else {}
    kind = data.get('kind')
    if kind not in KINDS:
        return JsonResponse({'success': False, 'error': f"kind must be one of {sorted(KINDS)}"}, status=400)
    listing_id = data.get('listing_id')
    try:
        listing_id = int(listing_id) if listing_id not in (None, '') else None
    except (ValueError, TypeError):
        listing_id = None
    event = record_failure_event(
        request.user, kind, listing_id=listing_id, reason=data.get('reason'),
        occurred_at=_parse_dt(data.get('occurred_at')) or timezone.now(),
    )
    if event is None:
        return JsonResponse({'success': False, 'error': 'could not record event'}, status=500)
    return JsonResponse({'success': True, 'event_id': event.id}, status=201)
