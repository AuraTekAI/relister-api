"""
Web Push: dealers' extensions subscribe here; admins broadcast messages / update
nudges. Uses VAPID (no Firebase project needed). Every push must carry a visible
notification (Chrome's userVisibleOnly rule), so `title`/`body` are required.
"""

import json

from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from .models import PushSubscription

User = get_user_model()


@api_view(['GET'])
@permission_classes([AllowAny])
def vapid_public_key(request):
    """GET /api/push/vapid-key/ — the extension needs this to subscribe."""
    return Response({'public_key': getattr(settings, 'VAPID_PUBLIC_KEY', '')})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def subscribe(request):
    """
    POST /api/push/subscribe/
    Body: { endpoint, keys: { p256dh, auth }, install_type?, extension_version?, user_agent? }
    Upserts by endpoint and binds it to the authenticated dealer.
    """
    data = request.data if isinstance(request.data, dict) else {}
    endpoint = data.get('endpoint')
    keys = data.get('keys') or {}
    p256dh = keys.get('p256dh') or data.get('p256dh')
    auth = keys.get('auth') or data.get('auth')
    if not endpoint or not p256dh or not auth:
        return Response({'success': False, 'error': 'endpoint and keys.p256dh/auth are required'}, status=400)

    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user,
            'p256dh': p256dh,
            'auth': auth,
            'install_type': data.get('install_type'),
            'extension_version': data.get('extension_version'),
            'user_agent': (data.get('user_agent') or '')[:300],
        },
    )
    return Response({'success': True})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def unsubscribe(request):
    """POST /api/push/unsubscribe/ — body { endpoint }. Best-effort cleanup."""
    endpoint = (request.data or {}).get('endpoint') if isinstance(request.data, dict) else None
    if endpoint:
        PushSubscription.objects.filter(endpoint=endpoint).delete()
    return Response({'success': True})


def _send_one(sub, payload):
    """Send to a single subscription. Returns (ok, gone) — gone=True means prune it."""
    from pywebpush import webpush, WebPushException
    try:
        webpush(
            subscription_info={
                'endpoint': sub.endpoint,
                'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={'sub': getattr(settings, 'VAPID_SUBJECT', 'mailto:support@autorelister.com.au')},
            timeout=10,
        )
        return True, False
    except WebPushException as e:
        status = getattr(getattr(e, 'response', None), 'status_code', None)
        # 404/410 → subscription is dead; prune it.
        return False, status in (404, 410)
    except Exception:  # noqa: BLE001
        return False, False


@api_view(['POST'])
@permission_classes([IsAdminUser])
def send_push(request):
    """
    POST /api/push/send/  (admin)
    Body: {
      target: <user_id> | "all",
      type: "message" | "update",     # "update" also triggers requestUpdateCheck in the SW
      title, body,
      url?                            # optional click-through
    }
    """
    if not getattr(settings, 'VAPID_PRIVATE_KEY', ''):
        return Response({'success': False, 'error': 'server VAPID keys not configured'}, status=500)

    data = request.data if isinstance(request.data, dict) else {}
    target = data.get('target')
    ptype = data.get('type', 'message')
    title = data.get('title') or ('Update available' if ptype == 'update' else 'Auto Relister')
    body = data.get('body') or ''
    if ptype not in ('message', 'update'):
        return Response({'success': False, 'error': 'type must be message or update'}, status=400)

    qs = PushSubscription.objects.all()
    if target != 'all':
        try:
            qs = qs.filter(user_id=int(target))
        except (ValueError, TypeError):
            return Response({'success': False, 'error': 'target must be a user id or "all"'}, status=400)

    payload = {'type': ptype, 'title': title, 'body': body, 'url': data.get('url')}
    sent = 0
    pruned = 0
    prune_ids = []
    for sub in qs:
        ok, gone = _send_one(sub, payload)
        if ok:
            sent += 1
        if gone:
            prune_ids.append(sub.id)
    if prune_ids:
        pruned = PushSubscription.objects.filter(id__in=prune_ids).delete()[0]

    return Response({
        'success': True,
        'target': target,
        'type': ptype,
        'sent': sent,
        'pruned_dead': pruned,
        'total_subscriptions': qs.count() if target == 'all' else len(prune_ids) + sent,
    })
