from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
