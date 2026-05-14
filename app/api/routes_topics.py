from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse, Response

from app.api.routes_assets import asset_head_response, validate_object_key, _stream_minio_object
from app.core.config import get_settings
from app.models.job_models import FutureEndpointResponse
from app.models.job_models import JobStatus
from app.models.topic_models import TopicListPayload
from app.services.job_service import ensure_job_exists, update_job_state
from app.services.progress_service import update_progress
from app.services.topic_service import (
    approve_topic as approve_single_topic_for_job,
    approve_topics as approve_topics_for_job,
    extract_topics_for_job,
    find_topic_preview_pdf,
    read_topics,
    save_topics,
    topic_preview_info,
)

router = APIRouter(prefix="/api/jobs", tags=["topics"])


def future() -> FutureEndpointResponse:
    return FutureEndpointResponse()


@router.post("/{job_id}/extract/topics")
def extract_topics(job_id: str, background_tasks: BackgroundTasks):
    ensure_job_exists(job_id)
    update_job_state(job_id, status=JobStatus.extracting_topics, stage="extracting_topics")
    update_progress(
        job_id,
        status=JobStatus.extracting_topics,
        stage="extracting_topics",
        message="Đang trích xuất chủ đề, vui lòng chờ...",
        percent=5,
    )
    background_tasks.add_task(extract_topics_for_job, job_id)
    return {
        "ok": True,
        "job_id": job_id,
        "status": JobStatus.extracting_topics,
        "message": "Đang trích xuất chủ đề, vui lòng chờ...",
    }


@router.get("/{job_id}/topics")
def get_topics(job_id: str):
    try:
        return read_topics(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{job_id}/topics")
def update_topics(job_id: str, payload: TopicListPayload):
    return save_topics(
        job_id,
        [topic.model_dump(mode="json", exclude_none=True) for topic in payload.topics],
    )


@router.get("/{job_id}/topics/{topic_num}/preview")
def preview_topic_pdf(job_id: str, topic_num: int):
    try:
        pdf_path = find_topic_preview_pdf(job_id, topic_num)
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name,
            content_disposition_type="inline",
        )
    except FileNotFoundError as exc:
        try:
            info = topic_preview_info(job_id, topic_num)
            object_key = info.get("asset_object_key")
            if object_key:
                safe_key = validate_object_key(object_key)
                return _stream_minio_object(get_settings().minio_bucket, safe_key)
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "ok": False,
                    "message": f"Không tìm thấy file PDF preview cho Topic {topic_num:02d}.",
                    "checked_paths": (info.get("debug") or {}).get("checked_paths", []),
                    "pdf_candidates": (info.get("debug") or {}).get("pdf_candidates", []),
                },
            )
        except FileNotFoundError:
            info = {}
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "ok": False,
                "message": f"Không tìm thấy file PDF preview cho Topic {topic_num:02d}.",
                "detail": str(exc),
                "checked_paths": (info.get("debug") or {}).get("checked_paths", []),
                "pdf_candidates": (info.get("debug") or {}).get("pdf_candidates", []),
            },
        )


@router.head("/{job_id}/topics/{topic_num}/preview")
def head_topic_pdf(job_id: str, topic_num: int):
    try:
        pdf_path = find_topic_preview_pdf(job_id, topic_num)
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(pdf_path.stat().st_size),
                "Content-Disposition": f'inline; filename="{pdf_path.name}"',
            },
        )
    except FileNotFoundError as exc:
        try:
            info = topic_preview_info(job_id, topic_num)
            object_key = info.get("asset_object_key")
            if object_key:
                safe_key = validate_object_key(object_key)
                return asset_head_response(get_settings().minio_bucket, safe_key)
        except FileNotFoundError:
            pass
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy file PDF preview cho Topic {topic_num:02d}.") from exc


@router.get("/{job_id}/topics/{topic_num}/preview-info")
def get_topic_preview_info(job_id: str, topic_num: int):
    # topic_preview_info now gracefully handles missing topics_partial.json,
    # so we only raise 404 if the job itself is not found.
    try:
        return topic_preview_info(job_id, topic_num)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post("/{job_id}/topics/approve")
def approve_topics(
    job_id: str,
    payload: dict[str, Any] | TopicListPayload | None = Body(default=None),
):
    try:
        topics = None
        topic_nums = None
        if payload is not None:
            if isinstance(payload, TopicListPayload):
                topics = [topic.model_dump(mode="json", exclude_none=True) for topic in payload.topics]
            elif isinstance(payload, dict):
                raw_topics = payload.get("topics")
                if isinstance(raw_topics, list):
                    topics = [dict(topic) for topic in raw_topics if isinstance(topic, dict)]
                raw_topic_nums = payload.get("topic_nums")
                if isinstance(raw_topic_nums, list):
                    topic_nums = raw_topic_nums
        return approve_topics_for_job(job_id, topics=topics, topic_nums=topic_nums)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{job_id}/topics/{topic_num}/approve")
def approve_topic(job_id: str, topic_num: int):
    try:
        return approve_single_topic_for_job(job_id, topic_num)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
