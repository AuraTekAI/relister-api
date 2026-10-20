from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import activity
from .models import ExtensionLog
from .serializers import ExtensionLogSerializer


class ExtensionLogCreateView(APIView):
    """
    POST /api/extension-logs/
    Stores a log line for the authenticated user.
    The user is taken from the JWT — the client only sends `log`.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Store an extension log entry for the authenticated user",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['log'],
            properties={'log': openapi.Schema(type=openapi.TYPE_STRING)},
        ),
        responses={201: ExtensionLogSerializer, 400: "Validation error"},
    )
    def post(self, request):
        log_text = request.data.get('log') if isinstance(request.data, dict) else None
        if not log_text or not str(log_text).strip():
            return Response(
                {'log': ['This field is required.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = ExtensionLog.objects.create(user=request.user, log=str(log_text))
        return Response(
            ExtensionLogSerializer(record).data,
            status=status.HTTP_201_CREATED,
        )


class ExtensionActivityView(APIView):
    """
    POST /api/extension-logs/activity/   record a usage event for the authenticated user
        {"event": "open"}                         — extension opened (first_seen / last_seen)
        {"event": "listed", "count": 1}           — also: relisted, deleted, other
    GET  /api/extension-logs/activity/?days=14   the authenticated user's daily summary
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Record an extension usage event for the authenticated user",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['event'],
            properties={
                'event': openapi.Schema(type=openapi.TYPE_STRING, enum=list(activity.VALID_EVENTS)),
                'count': openapi.Schema(type=openapi.TYPE_INTEGER, default=1),
            },
        ),
        responses={200: "Today's activity row", 400: "Validation error"},
    )
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        event = str(data.get('event') or '').strip().lower()
        count = data.get('count', 1)
        try:
            row = activity.record_activity(request.user, event, count)
        except ValueError as exc:
            return Response({'success': False, 'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, 'today': activity.serialize_day(row)}, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_summary="Daily usage summary for the authenticated user",
        manual_parameters=[openapi.Parameter('days', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, default=14)],
    )
    def get(self, request):
        try:
            days = int(request.GET.get('days', 14))
        except (TypeError, ValueError):
            return Response({'success': False, 'error': 'days must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, **activity.daily_summary(request.user, days)})
