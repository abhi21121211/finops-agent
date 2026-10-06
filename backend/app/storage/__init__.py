"""S3-compatible object storage (SeaweedFS locally, Supabase Storage / S3 in production).

Keys are always prefixed by tenant id. boto3 is sync, so calls run in a thread.
"""

import asyncio
from functools import lru_cache
from typing import Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings


class Storage(Protocol):
    async def ensure_bucket(self) -> None: ...
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    def presigned_url(self, key: str, expires_s: int = 900) -> str: ...


def tenant_key(tenant_id: object, *parts: object) -> str:
    return "/".join([f"tenants/{tenant_id}", *map(str, parts)])


class S3Storage:
    def __init__(self) -> None:
        s = get_settings()
        self.bucket = s.s3_bucket
        common = dict(
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._client = boto3.client("s3", endpoint_url=s.s3_endpoint, **common)
        # Signs URLs with the host the browser can reach; never used for requests.
        self._public = boto3.client("s3", endpoint_url=s.s3_public_endpoint, **common)

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self.bucket)

        await asyncio.to_thread(_ensure)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

        return await asyncio.to_thread(_get)

    def presigned_url(self, key: str, expires_s: int = 900) -> str:
        return self._public.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s
        )


@lru_cache
def get_storage() -> Storage:
    return S3Storage()
