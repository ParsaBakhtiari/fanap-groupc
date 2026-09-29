from functools import lru_cache

import boto3

from .config import get_settings


@lru_cache
def object_storage_client():
    settings = get_settings()
    if not settings.object_storage_access_key or not settings.object_storage_secret_key:
        raise RuntimeError("object storage credentials are not configured")
    return boto3.client(
        "s3",
        endpoint_url=settings.object_storage_endpoint,
        region_name=settings.object_storage_region,
        aws_access_key_id=settings.object_storage_access_key,
        aws_secret_access_key=settings.object_storage_secret_key,
    )
