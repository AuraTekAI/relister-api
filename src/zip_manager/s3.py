import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from django.conf import settings

logger = logging.getLogger(__name__)


def s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )


def s3_key(filename):
    return f"{settings.AWS_S3_ZIP_PREFIX}{filename}"


def s3_delete(key):
    """Delete an S3 object. Returns (success: bool, error_msg: str|None)."""
    try:
        s3_client().delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key,
        )
        return True, None
    except (ClientError, BotoCoreError, NoCredentialsError) as exc:
        logger.error("S3 delete failed for key=%s: %s", key, exc)
        return False, str(exc)


def s3_upload(file_obj, key):
    """
    Upload a file-like object to S3.
    Returns (success: bool, error_msg: str|None).
    Caller must seek(0) before calling if needed.
    """
    try:
        s3_client().upload_fileobj(
            file_obj,
            settings.AWS_STORAGE_BUCKET_NAME,
            key,
            ExtraArgs={'ContentType': 'application/zip'},
        )
        return True, None
    except NoCredentialsError:
        msg = "S3 upload failed: AWS credentials are not configured."
        logger.error(msg)
        return False, msg
    except (ClientError, BotoCoreError) as exc:
        msg = f"S3 upload failed: {exc}"
        logger.error("S3 upload failed for key=%s: %s", key, exc)
        return False, msg
