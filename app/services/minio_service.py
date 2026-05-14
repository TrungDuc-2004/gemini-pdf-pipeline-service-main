from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote, urlparse

from app.core.config import get_settings


def _endpoint_host(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme:
        return parsed.netloc
    return endpoint.replace("http://", "").replace("https://", "").strip("/")


def get_minio_client():
    from minio import Minio

    settings = get_settings()
    return Minio(
        _endpoint_host(settings.minio_endpoint),
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def ensure_bucket(bucket_name: str) -> None:
    client = get_minio_client()
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)


def file_size(local_path: str | Path) -> int:
    return Path(local_path).stat().st_size


def build_public_url(bucket: str, object_key: str) -> str:
    public_url = get_settings().minio_public_url.rstrip("/")
    encoded_key = quote(object_key, safe="/")
    return f"{public_url}/{bucket}/{encoded_key}"


def object_exists(bucket: str, object_key: str) -> bool:
    try:
        stat_object(bucket, object_key)
        return True
    except Exception as exc:
        code = getattr(exc, "code", "")
        if code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            return False
        raise


def stat_object(bucket: str, object_key: str):
    client = get_minio_client()
    return client.stat_object(bucket, object_key)


def get_object_stream(bucket: str, object_key: str):
    client = get_minio_client()
    return client.get_object(bucket, object_key)


def infer_content_type(object_key: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(object_key)
    return guessed or fallback


def get_presigned_url(bucket: str, object_key: str, expires_seconds: int = 3600) -> str:
    from datetime import timedelta

    client = get_minio_client()
    return client.presigned_get_object(bucket, object_key, expires=timedelta(seconds=expires_seconds))


def upload_file(
    local_path: str | Path,
    bucket: str,
    object_key: str,
    content_type: str = "application/octet-stream",
) -> dict:
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"Upload source not found: {path}")

    ensure_bucket(bucket)
    client = get_minio_client()
    size = file_size(path)
    client.fput_object(
        bucket_name=bucket,
        object_name=object_key,
        file_path=str(path),
        content_type=content_type,
    )
    return {
        "bucket": bucket,
        "object_key": object_key,
        "file_name": path.name,
        "url": build_public_url(bucket, object_key),
        "content_type": content_type,
        "size": size,
    }
