import logging

from django.utils import timezone

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .alerting import send_email
from .models import TeamAlertLog

logger = logging.getLogger('relister_views')

ALERT_SUBJECTS = {
    'duplicates_detected': '[Action Required] Client has duplicate FB listings',
    'verification_required': '[Action Required] Client FB account needs verification',
    'aged_listings_detected': '[Action Required] Client has listings older than 7 days',
}

KNOWN_ALERT_TYPES = set(ALERT_SUBJECTS.keys())


@require_GET
def infrastructure_health(request):
    """
    GET /api/team-alerts/health/?component=redis   (no auth — polled by the
    external uptime cron, monitoring/uptime_check.py)

    Runs the infrastructure checks synchronously in the WEB process, so it
    works when Redis (Celery's broker) is down and the Beat-driven check
    cannot run. 200 when every requested component answers, 503 otherwise.
    The response names the component and the error only — nothing sensitive.
    """
    from .tasks import CHECKS

    requested = [c for c in request.GET.get('component', '').split(',') if c.strip()] or list(CHECKS)
    unknown = [c for c in requested if c not in CHECKS]
    if unknown:
        return JsonResponse({'error': f"unknown component(s): {', '.join(unknown)}"}, status=400)

    results, healthy = {}, True
    for component in requested:
        try:
            CHECKS[component]()
            results[component] = 'ok'
        except Exception as exc:
            healthy = False
            results[component] = f"down: {type(exc).__name__}: {exc}"
            logger.error(f"infra_health endpoint: {component} check FAILED: {type(exc).__name__}: {exc}")
    results['checked_at'] = timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')
    return JsonResponse(results, status=200 if healthy else 503)


def _build_email_body(alert_type, user, active_listings, inactive_listings, old_listings_count):
    client_label = user.dealership_name or user.email
    lines = [
        f"Alert type: {alert_type}",
        f"Client: {client_label} ({user.email})",
        f"Active listings: {active_listings}",
        f"Inactive listings: {inactive_listings}",
    ]
    if alert_type == 'aged_listings_detected' and old_listings_count is not None:
        lines.append(f"Listings older than 7 days: {old_listings_count}")
    return '\n'.join(lines) + '\n'


class TeamAlertView(APIView):
    """
    POST /api/team-alerts/
    Sends a one-per-day alert email to the support team for a given client + alert_type.
    Idempotent: returns {"status": "ok"} immediately if already sent today.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Send a team alert email (deduped per client+type per day)",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['alert_type', 'active_listings', 'inactive_listings'],
            properties={
                'alert_type': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    enum=list(KNOWN_ALERT_TYPES),
                ),
                'active_listings': openapi.Schema(type=openapi.TYPE_INTEGER),
                'inactive_listings': openapi.Schema(type=openapi.TYPE_INTEGER),
                'old_listings_count': openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description='Only sent for aged_listings_detected',
                ),
            },
        ),
        responses={200: openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={'status': openapi.Schema(type=openapi.TYPE_STRING)},
        )},
    )
    def post(self, request):
        from rest_framework import status as http_status

        alert_type = request.data.get('alert_type', '')
        active_listings = request.data.get('active_listings')
        inactive_listings = request.data.get('inactive_listings')
        old_listings_count = request.data.get('old_listings_count')

        errors = {}
        if not alert_type or not str(alert_type).strip():
            errors['alert_type'] = ['This field is required.']
        elif alert_type not in KNOWN_ALERT_TYPES:
            errors['alert_type'] = [f'Unknown alert_type. Valid values: {", ".join(sorted(KNOWN_ALERT_TYPES))}.']
        if active_listings is None:
            errors['active_listings'] = ['This field is required.']
        if inactive_listings is None:
            errors['inactive_listings'] = ['This field is required.']
        if errors:
            return Response(errors, status=http_status.HTTP_400_BAD_REQUEST)

        today = timezone.localdate()
        already_sent = TeamAlertLog.objects.filter(
            user=request.user,
            alert_type=alert_type,
            sent_at__date=today,
        ).exists()

        if not already_sent:
            user = request.user
            client_label = user.dealership_name or user.email
            subject = ALERT_SUBJECTS[alert_type]
            body = _build_email_body(
                alert_type, user,
                active_listings, inactive_listings, old_listings_count,
            )
            # Facebook-related → KAZ_ONLY via the central sender. support@ is
            # never used (Flashpost: recipient_suppressed). The log row — which
            # is also the once-per-day de-dup — is written ONLY when Flashpost
            # accepted the email, so a failed send is retried on the next report.
            if not send_email('kaz', f"{subject} — {client_label}", body):
                logger.error(f"team-alerts: {alert_type} for {client_label} NOT delivered — not logged, will retry on next report.")
                return Response({'status': 'not_sent'})

            TeamAlertLog.objects.create(
                user=user,
                alert_type=alert_type,
                active_listings=int(active_listings),
                inactive_listings=int(inactive_listings),
                old_listings_count=int(old_listings_count) if old_listings_count is not None else None,
            )

        return Response({'status': 'ok'})
