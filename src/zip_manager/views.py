import logging

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from django.conf import settings
from django.db import DatabaseError

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ZipFile
from .s3 import s3_delete, s3_key, s3_upload, s3_client
from .serializers import ZipFileSerializer, ZipFileUploadSerializer

logger = logging.getLogger(__name__)


class ZipFileUploadView(APIView):
    """
    POST  /api/zip-manager/upload/
    Admin uploads a new .zip file. Filename must follow <name>_v<number>.zip.
    If a record with the same base_name already exists the upload is treated
    as an update and the version must be strictly greater than the stored one.
    """
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        operation_summary="Upload or update a ZIP file (admin only)",
        manual_parameters=[
            openapi.Parameter(
                'file', openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="ZIP file following <name>_v<number>.zip naming convention",
            )
        ],
        responses={
            201: ZipFileSerializer,
            200: ZipFileSerializer,
            400: "Validation error",
            502: "S3 error",
            503: "Database error",
        },
    )
    def post(self, request):
        if 'file' not in request.data:
            return Response(
                {'detail': 'No file was submitted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ZipFileUploadSerializer(data=request.data)
        if not serializer.is_valid():
            file_errors = serializer.errors.get('file')
            if file_errors:
                return Response(
                    {'detail': file_errors[0]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        uploaded_file = serializer.validated_data['file']
        filename = uploaded_file.name

        try:
            base_name, version = ZipFile.parse_filename(filename)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            existing = ZipFile.objects.filter(base_name=base_name).first()
        except DatabaseError as exc:
            logger.error("DB error looking up base_name=%s: %s", base_name, exc)
            return Response(
                {'detail': 'Database error. Please try again.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if existing and version <= existing.version:
            return Response(
                {
                    'detail': (
                        f"Update rejected. Existing version is v{existing.version}. "
                        f"Please upload a newer version (v{existing.version + 1} or above)."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        key = s3_key(filename)
        uploaded_file.seek(0)
        ok, err = s3_upload(uploaded_file, key)
        if not ok:
            return Response({'detail': err}, status=status.HTTP_502_BAD_GATEWAY)

        if existing:
            old_key = existing.s3_key
            existing.filename = filename
            existing.version = version
            existing.s3_key = key
            try:
                existing.save()
            except DatabaseError as exc:
                logger.error("DB save failed after S3 upload, rolling back key=%s: %s", key, exc)
                s3_delete(key)
                return Response(
                    {'detail': 'Database error while saving update. The upload has been rolled back.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            if old_key != key:
                ok, err = s3_delete(old_key)
                if not ok:
                    logger.warning(
                        "Old S3 object not deleted (key=%s). New file is live. Manual cleanup may be needed.",
                        old_key,
                    )

            return Response(ZipFileSerializer(existing).data, status=status.HTTP_200_OK)

        try:
            record = ZipFile.objects.create(
                filename=filename,
                base_name=base_name,
                version=version,
                s3_key=key,
            )
        except DatabaseError as exc:
            logger.error("DB create failed after S3 upload, rolling back key=%s: %s", key, exc)
            s3_delete(key)
            return Response(
                {'detail': 'Database error while saving record. The upload has been rolled back.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(ZipFileSerializer(record).data, status=status.HTTP_201_CREATED)


class ZipFileListView(APIView):
    """
    GET /api/zip-manager/
    Any authenticated user can list all uploaded ZIP files.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="List all ZIP files",
        responses={200: ZipFileSerializer(many=True)},
    )
    def get(self, request):
        try:
            records = ZipFile.objects.all()
            return Response(ZipFileSerializer(records, many=True).data)
        except DatabaseError as exc:
            logger.error("DB error listing ZIP files: %s", exc)
            return Response(
                {'detail': 'Database error. Please try again.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class ZipFileDeleteView(APIView):
    """
    DELETE /api/zip-manager/<id>/
    Admin deletes a ZIP record from the DB and the file from S3.
    """
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_summary="Delete a ZIP file (admin only)",
        responses={
            200: openapi.Response(description="File deleted successfully"),
            404: "Not found",
            502: "S3 deletion failed",
            503: "Database error",
        },
    )
    def delete(self, request, pk):
        try:
            record = ZipFile.objects.get(pk=pk)
        except ZipFile.DoesNotExist:
            return Response({'detail': 'File not found.'}, status=status.HTTP_404_NOT_FOUND)
        except DatabaseError as exc:
            logger.error("DB error fetching ZipFile pk=%s: %s", pk, exc)
            return Response(
                {'detail': 'Database error. Please try again.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        key = record.s3_key

        try:
            record.delete()
        except DatabaseError as exc:
            logger.error("DB delete failed for ZipFile pk=%s: %s", pk, exc)
            return Response(
                {'detail': 'Database error while deleting record. Please try again.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        ok, err = s3_delete(key)
        if not ok:
            logger.warning("S3 object not deleted (key=%s). Manual cleanup needed. Error: %s", key, err)
            return Response(
                {
                    'detail': (
                        'Record deleted from database but the S3 file could not be removed. '
                        'Please clean it up manually.'
                    )
                },
                status=status.HTTP_200_OK,
            )

        return Response({'detail': 'File deleted successfully.'}, status=status.HTTP_200_OK)


class ZipFileDownloadView(APIView):
    """
    GET /api/zip-manager/<id>/download/
    Any authenticated user downloads the ZIP via a presigned S3 URL.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get a presigned download URL for a ZIP file",
        responses={
            200: openapi.Response(
                description="Presigned URL",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'filename': openapi.Schema(type=openapi.TYPE_STRING),
                        'download_url': openapi.Schema(type=openapi.TYPE_STRING),
                        'expires_in_seconds': openapi.Schema(type=openapi.TYPE_INTEGER),
                    },
                ),
            ),
            404: "File not found",
            502: "Presigned URL generation failed",
            503: "Database error",
        },
    )
    def get(self, request, pk):
        try:
            record = ZipFile.objects.get(pk=pk)
        except ZipFile.DoesNotExist:
            return Response({'detail': 'File not found.'}, status=status.HTTP_404_NOT_FOUND)
        except DatabaseError as exc:
            logger.error("DB error fetching ZipFile pk=%s: %s", pk, exc)
            return Response(
                {'detail': 'Database error. Please try again.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        expiry = getattr(settings, 'AWS_S3_PRESIGNED_URL_EXPIRY', 3600)
        safe_filename = record.filename.replace('\\', '\\\\').replace('"', '\\"')

        try:
            url = s3_client().generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                    'Key': record.s3_key,
                    'ResponseContentDisposition': f'attachment; filename="{safe_filename}"',
                },
                ExpiresIn=expiry,
            )
        except NoCredentialsError:
            logger.error("AWS credentials not configured correctly.")
            return Response(
                {'detail': 'Could not generate download URL: AWS credentials are not configured.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.error("Presigned URL generation failed for key=%s: %s", record.s3_key, exc)
            return Response(
                {'detail': f"Could not generate download URL: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            'filename': record.filename,
            'download_url': url,
            'expires_in_seconds': expiry,
        })
