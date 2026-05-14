from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.services.minio_service import get_object_stream, infer_content_type, stat_object

router = APIRouter(prefix="/api/assets", tags=["assets"])

ALLOWED_OBJECT_PREFIXES = ("documents/", "images/", "videos/")


def validate_object_key(object_key: str) -> str:
    key = (object_key or "").strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="object_key is required.")
    if ".." in key or key.startswith("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsafe object_key.")
    if not key.startswith(ALLOWED_OBJECT_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsafe object_key. Allowed prefixes: documents/, images/, videos/.",
        )
    return key


def _content_type_for_object(object_key: str, stat: object | None = None) -> str:
    stat_type = getattr(stat, "content_type", None)
    if stat_type and stat_type != "application/octet-stream":
        return stat_type
    return infer_content_type(object_key)


def _stream_minio_object(bucket: str, object_key: str) -> StreamingResponse:
    try:
        stat = stat_object(bucket, object_key)
        response = get_object_stream(bucket, object_key)
    except Exception as exc:
        code = getattr(exc, "code", "")
        if code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Object not found: {bucket}/{object_key}",
            ) from exc
        raise

    content_type = _content_type_for_object(object_key, stat)
    headers = {
        "Content-Disposition": f'inline; filename="{object_key.rsplit("/", 1)[-1]}"',
    }
    size = getattr(stat, "size", None)
    if size is not None:
        headers["Content-Length"] = str(size)

    def iterator() -> Iterator[bytes]:
        try:
            for chunk in response.stream(1024 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(iterator(), media_type=content_type, headers=headers)


def asset_head_response(bucket: str, object_key: str) -> Response:
    try:
        stat = stat_object(bucket, object_key)
    except Exception as exc:
        code = getattr(exc, "code", "")
        if code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Object not found: {bucket}/{object_key}",
            ) from exc
        raise
    content_type = _content_type_for_object(object_key, stat)
    headers = {
        "Content-Type": content_type,
        "Content-Disposition": f'inline; filename="{object_key.rsplit("/", 1)[-1]}"',
    }
    size = getattr(stat, "size", None)
    if size is not None:
        headers["Content-Length"] = str(size)
    return Response(status_code=status.HTTP_200_OK, headers=headers)


@router.get("/preview")
def preview_asset(
    object_key: str = Query(..., min_length=1),
    bucket: str | None = Query(default=None),
):
    settings = get_settings()
    safe_key = validate_object_key(object_key)
    target_bucket = bucket or settings.minio_bucket
    return _stream_minio_object(target_bucket, safe_key)


@router.head("/preview")
def head_asset_preview(
    object_key: str = Query(..., min_length=1),
    bucket: str | None = Query(default=None),
):
    settings = get_settings()
    safe_key = validate_object_key(object_key)
    target_bucket = bucket or settings.minio_bucket
    return asset_head_response(target_bucket, safe_key)
