"""
Admin-only diagnostics + remote-control REST endpoints, consumed by the
debugging MCP server (so Claude can investigate why a dealer's extension isn't
publishing / deleting / relisting, and drive the control channel).

All endpoints require IsAdminUser. They deliberately live in their own module to
keep the public log-sink view (`views.py`) small.
"""

import asyncio
import json
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from django.contrib.auth import get_user_model

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .consumers import KNOWN_COMMANDS
from .models import ExtensionLog

User = get_user_model()

# How long to wait for the extension to ack a remote command before giving up.
COMMAND_ACK_TIMEOUT = 100


def _dealer_name(u):
    return u.dealership_name or u.contact_person_name or None


def _resolve_user(request):
    """Resolve the target dealer from ?user_id= or ?email=. Returns (user, error_response)."""
    uid = request.GET.get('user_id')
    email = request.GET.get('email')
    if uid:
        try:
            return User.objects.get(pk=int(uid)), None
        except (User.DoesNotExist, ValueError, TypeError):
            return None, Response({'success': False, 'error': f'no user with id {uid}'}, status=404)
    if email:
        u = User.objects.filter(email__iexact=email.strip()).first()
        if not u:
            return None, Response({'success': False, 'error': f'no user with email {email}'}, status=404)
        return u, None
    return None, Response({'success': False, 'error': 'user_id or email is required'}, status=400)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def get_extension_logs(request):
    """
    GET /api/extension-logs/admin/?user_id=&email=&limit=&contains=
    Recent extension log rows for a dealer, newest first. Each row's `log` is the
    raw payload the extension posted (usually a JSON string with a message +
    breadcrumb buffer). Optional `contains` does a case-insensitive substring
    filter so Claude can search for a title / error.
    """
    user, err = _resolve_user(request)
    if err:
        return err
    try:
        limit = min(int(request.GET.get('limit', 50)), 500)
    except (ValueError, TypeError):
        limit = 50
    qs = ExtensionLog.objects.filter(user=user)
    contains = (request.GET.get('contains') or '').strip()
    if contains:
        qs = qs.filter(log__icontains=contains)
    rows = list(qs.order_by('-created_at')[:limit])
    return Response({
        'success': True,
        'user_id': user.id,
        'email': user.email,
        'count': len(rows),
        'logs': [
            {'id': r.id, 'created_at': r.created_at.isoformat(), 'log': r.log}
            for r in rows
        ],
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def get_dealer_meta(request):
    """
    GET /api/extension-logs/dealer-meta/?user_id=&email=
    Lightweight dealer context: identity, subscription state, last extension sync
    status (incl. why Facebook is broken, if it is), and version. Pairs with the
    heavy fb-snapshots endpoint for full listing data.
    """
    user, err = _resolve_user(request)
    if err:
        return err

    sub = getattr(user, 'subscription', None)
    sync = getattr(user, 'ext_sync_status', None)

    return Response({
        'success': True,
        'user': {
            'user_id': user.id,
            'email': user.email,
            'name': _dealer_name(user),
            'is_active': user.is_active,
            'custom_domain_url': getattr(user, 'custom_domain_url', None),
            'date_joined': user.date_joined.isoformat() if getattr(user, 'date_joined', None) else None,
        },
        'subscription': None if not sub else {
            'status': sub.status,
            'plan': getattr(getattr(sub, 'plan', None), 'name', None),
            'current_period_end': sub.current_period_end.isoformat() if sub.current_period_end else None,
            'cancel_at_period_end': sub.cancel_at_period_end,
            'listing_count': sub.listing_count,
        },
        'sync_status': None if not sync else {
            'mode': sync.mode,
            'status': sync.status,
            'status_detail': sync.status_detail,
            'fb_count': sync.fb_count,
            'unpublished_count': sync.unpublished_count,
            'extension_version': sync.extension_version,
            'synced_at': sync.synced_at.isoformat() if sync.synced_at else None,
        },
    })


def _send_command_await_ack(uid, command, payload, timeout=COMMAND_ACK_TIMEOUT):
    """
    Push a command into the dealer's extension (ext-<uid> group) and wait for its
    ack, which the extension fans out to the observer group (obs-<uid>). We join
    obs-<uid> on a throwaway channel, filter for our correlation id, and return
    the ack — so Claude sees the real result ("published", "already on Facebook —
    skipped", "no listing found", …). Times out if the extension is offline.
    """
    channel_layer = get_channel_layer()
    cmd_id = f"mcp-{uuid.uuid4().hex[:8]}"
    ext_group = f"ext-{uid}"
    obs_group = f"obs-{uid}"

    async def _run():
        temp = await channel_layer.new_channel()
        await channel_layer.group_add(obs_group, temp)
        try:
            await channel_layer.group_send(ext_group, {
                "type": "command.message",
                "data": {"type": "command", "command": command, "payload": payload, "id": cmd_id},
            })
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return {"ok": False, "detail": "timed out waiting for the extension — is it online?", "timed_out": True}
                try:
                    event = await asyncio.wait_for(channel_layer.receive(temp), timeout=remaining)
                except asyncio.TimeoutError:
                    return {"ok": False, "detail": "timed out waiting for the extension — is it online?", "timed_out": True}
                data = event.get("data") if isinstance(event, dict) else None
                if isinstance(data, dict) and str(data.get("id")) == cmd_id:
                    # report_status replies with a `status` message (no ack).
                    if data.get("type") == "status":
                        return {"ok": True, "command": command, "status": data.get("status")}
                    if data.get("type") == "ack":
                        return {"ok": bool(data.get("ok")), "detail": data.get("detail"), "command": command}
        finally:
            await channel_layer.group_discard(obs_group, temp)

    return async_to_sync(_run)()


@api_view(['POST'])
@permission_classes([IsAdminUser])
def send_extension_command(request):
    """
    POST /api/extension-logs/command/
    Body: { user_id | email, command, payload?, wait? }
    Drives the real-time control channel: enqueues a command to the dealer's
    running extension. If wait is not false, blocks until the extension acks and
    returns the result. Only KNOWN_COMMANDS are accepted.
    """
    body = request.data if isinstance(request.data, dict) else {}
    command = body.get('command')
    if command not in KNOWN_COMMANDS:
        return Response({'success': False, 'error': f'unknown command: {command}',
                         'known_commands': sorted(KNOWN_COMMANDS)}, status=400)

    uid = body.get('user_id')
    if not uid and body.get('email'):
        u = User.objects.filter(email__iexact=str(body['email']).strip()).first()
        if not u:
            return Response({'success': False, 'error': f'no user with email {body["email"]}'}, status=404)
        uid = u.id
    if not uid:
        return Response({'success': False, 'error': 'user_id or email is required'}, status=400)
    try:
        uid = int(uid)
    except (ValueError, TypeError):
        return Response({'success': False, 'error': 'user_id must be an integer'}, status=400)

    payload = body.get('payload') or {}
    wait = body.get('wait', True)

    if not wait:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(f"ext-{uid}", {
            "type": "command.message",
            "data": {"type": "command", "command": command, "payload": payload, "id": f"mcp-async"},
        })
        return Response({'success': True, 'queued': True, 'waited': False, 'command': command})

    # A live extension answers status/refresh/cancel almost instantly; only the
    # publish/relist actions legitimately take a while. Use a short wait for the
    # quick ones so an OFFLINE dealer doesn't tie up a worker for the full window.
    quick = {'report_status', 'cancel', 'refresh', 'stop_auto', 'start_auto', 'start_video', 'stop_video'}
    timeout = 12 if command in quick else COMMAND_ACK_TIMEOUT
    result = _send_command_await_ack(uid, command, payload, timeout=timeout)
    return Response({'success': result.get('ok', False), 'waited': True, 'command': command,
                     'result': result})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def backend_health(request):
    """
    GET /api/extension-logs/health/
    Quick backend self-check for Claude: DB reachable, channel layer (Redis)
    reachable, and basic counts. Helps distinguish "extension problem" from
    "backend problem".
    """
    health = {'success': True, 'db': False, 'channel_layer': False}
    try:
        User.objects.exists()
        health['db'] = True
    except Exception as e:  # noqa: BLE001
        health['db_error'] = str(e)

    try:
        channel_layer = get_channel_layer()
        test_channel = async_to_sync(channel_layer.new_channel)()
        async_to_sync(channel_layer.send)(test_channel, {'type': 'ping'})
        async_to_sync(channel_layer.receive)(test_channel)
        health['channel_layer'] = True
    except Exception as e:  # noqa: BLE001
        health['channel_layer_error'] = str(e)

    try:
        from VehicleListing.models import ExtensionSyncStatus
        health['dealers_synced'] = ExtensionSyncStatus.objects.count()
        health['dealers_with_fb_errors'] = ExtensionSyncStatus.objects.exclude(status='ok').count()
    except Exception:  # noqa: BLE001
        pass

    health['known_commands'] = sorted(KNOWN_COMMANDS)
    return Response(health)
