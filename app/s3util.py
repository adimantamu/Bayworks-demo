from __future__ import annotations

import time

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import boto3

from app.config import settings

CORS = {
    "CORSRules": [
        {
            "AllowedOrigins": ["*"],
            "AllowedMethods": ["GET", "PUT", "POST", "HEAD"],
            "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag", "x-amz-request-id"],
            "MaxAgeSeconds": 3600,
        }
    ]
}


def _client(endpoint: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def internal_client():
    return _client(settings.s3_endpoint)


def ensure_bucket() -> None:
    last: Exception | None = None
    for _ in range(30):
        try:
            client = internal_client()
            try:
                client.head_bucket(Bucket=settings.s3_bucket)
            except ClientError:
                try:
                    client.create_bucket(Bucket=settings.s3_bucket)
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code", "")
                    if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise
            try:
                client.put_bucket_cors(Bucket=settings.s3_bucket, CORSConfiguration=CORS)
            except ClientError:
                pass
            return
        except (ClientError, BotoCoreError, OSError) as exc:
            last = exc
            time.sleep(1)
    raise last or RuntimeError("could not ensure S3 bucket")


def presign_put(key: str, content_type: str) -> str:
    client = _client(settings.s3_public_endpoint)
    return client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=900,
        HttpMethod="PUT",
    )


def put_bytes(key: str, body: bytes, content_type: str) -> None:
    internal_client().put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def get_bytes(key: str) -> bytes:
    obj = internal_client().get_object(Bucket=settings.s3_bucket, Key=key)
    return obj["Body"].read()


def object_key(rooftop_id: str, ro_number: str, content_hash: str, suffix: str) -> str:
    return f"{rooftop_id}/repairs/{ro_number}/{content_hash}{suffix}"
