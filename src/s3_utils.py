import os
import boto3
import asyncio
import logging
from botocore.exceptions import ClientError
from botocore.client import Config

logger = logging.getLogger(__name__)

def _get_s3_client():
    """Create and return a boto3 S3 client using environment variables."""
    endpoint_url = os.environ.get("S3_ENDPOINT_URL")
    access_key = os.environ.get("S3_ACCESS_KEY_ID")
    secret_key = os.environ.get("S3_SECRET_ACCESS_KEY")
    region_name = os.environ.get("S3_REGION", "auto")

    if not all([endpoint_url, access_key, secret_key]):
        raise ValueError("S3 credentials (S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY) are missing in .env")

    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region_name,
        config=Config(signature_version='s3v4')
    )

def _sync_upload_and_presign(filepath: str, object_name: str, expiration: int = 300) -> str:
    """Synchronously upload a file and generate a presigned URL."""
    bucket_name = os.environ.get("S3_BUCKET_NAME")
    if not bucket_name:
        raise ValueError("S3_BUCKET_NAME is missing in .env")

    s3_client = _get_s3_client()
    
    # Guess the correct MIME type (e.g. video/mp4) so OpenRouter accepts it
    import mimetypes
    content_type, _ = mimetypes.guess_type(filepath)
    if not content_type:
        content_type = "video/mp4"

    # Upload the file
    try:
        s3_client.upload_file(
            filepath, 
            bucket_name, 
            object_name, 
            ExtraArgs={'ContentType': content_type}
        )
    except ClientError as e:
        logger.error(f"Failed to upload to S3: {e}")
        raise RuntimeError(f"S3 Upload failed: {e}")

    # Generate presigned URL
    try:
        response = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': object_name},
            ExpiresIn=expiration
        )
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        raise RuntimeError(f"Presigned URL generation failed: {e}")

    return response

async def upload_video_and_get_presigned_url(filepath: str, object_name: str, expiration: int = 300) -> str:
    """
    Asynchronously upload a local video file to S3 and return a secure presigned HTTPS URL.
    The URL expires after `expiration` seconds (default 5 mins).
    """
    return await asyncio.to_thread(_sync_upload_and_presign, filepath, object_name, expiration)
