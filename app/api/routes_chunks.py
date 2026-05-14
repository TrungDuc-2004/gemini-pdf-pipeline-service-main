from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query, status
from fastapi.responses import FileResponse, Response

from app.api.routes_assets import asset_head_response, validate_object_key, _stream_minio_object
from app.core.config import get_settings
from app.models.chunk_models import ChunkAddPayload, ChunkListPayload, ChunkRecutPayload
from app.models.job_models import JobStatus
from app.services.chunk_service import (
    add_chunk as add_chunk_for_job,
    approve_chunk as approve_single_chunk_for_job,
    approve_chunks as approve_chunks_for_job,
    delete_chunk as delete_chunk_for_job,
    ensure_chunk_preconditions,
    extract_chunks_for_job,
    extract_chunks_for_lesson,
    find_chunk_preview_pdf,
    read_chunks,
    recut_chunk,
    save_chunks,
)
from app.services.chunk_metadata_service import save_final_chunks_after_kaggle, save_chunks_metadata_and_sync
from app.services.job_service import update_job_state
from app.services.progress_service import update_progress

router = APIRouter(prefix="/api/jobs", tags=["chunks"])


@router.post("/{job_id}/extract/chunks")
def extract_chunks(
    job_id: str,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] | None = Body(default=None),
):
    try:
        ensure_chunk_preconditions(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    lesson_nums = payload.get("lesson_nums") if isinstance(payload, dict) else None
    selected_lesson_num = lesson_nums[0] if isinstance(lesson_nums, list) and len(lesson_nums) == 1 else None
    stage = "extracting_chunks_for_lesson" if selected_lesson_num is not None else "extracting_chunks"
    message = (
        f"Đang trích xuất chunk cho Lesson {int(selected_lesson_num):02d}..."
        if selected_lesson_num is not None and str(selected_lesson_num).isdigit()
        else "Đang trích xuất chunk, vui lòng chờ..."
    )
    update_job_state(job_id, status=JobStatus.extracting_chunks, stage=stage)
    update_progress(
        job_id,
        status=JobStatus.extracting_chunks,
        stage=stage,
        message=message,
        percent=5,
    )
    if selected_lesson_num is not None:
        background_tasks.add_task(extract_chunks_for_lesson, job_id, selected_lesson_num)
    else:
        background_tasks.add_task(extract_chunks_for_job, job_id)
    return {
        "ok": True,
        "job_id": job_id,
        "status": JobStatus.extracting_chunks,
        "message": message,
    }


@router.post("/{job_id}/lessons/{lesson_num}/extract-chunks")
def extract_chunks_for_selected_lesson(job_id: str, lesson_num: int, background_tasks: BackgroundTasks):
    try:
        ensure_chunk_preconditions(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    update_job_state(job_id, status=JobStatus.extracting_chunks, stage="extracting_chunks_for_lesson")
    update_progress(
        job_id,
        status=JobStatus.extracting_chunks,
        stage="extracting_chunks_for_lesson",
        message=f"Đang trích xuất chunk cho Lesson {lesson_num:02d}...",
        percent=5,
    )
    background_tasks.add_task(extract_chunks_for_lesson, job_id, lesson_num)
    return {"ok": True, "job_id": job_id, "status": JobStatus.extracting_chunks, "lesson_num": lesson_num}


@router.get("/{job_id}/chunks")
def get_chunks(job_id: str):
    try:
        return read_chunks(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{job_id}/chunks")
def update_chunks(job_id: str, payload: ChunkListPayload):
    return save_chunks(
        job_id,
        [chunk.model_dump(mode="json", exclude_none=True) for chunk in payload.chunks],
    )


def _chunk_asset_key(job_id: str, chunk_id: str) -> str | None:
    try:
        chunks = read_chunks(job_id).get("chunks", [])
    except FileNotFoundError:
        return None
    for chunk in chunks:
        if str(chunk.get("chunk_id") or chunk.get("id")) == str(chunk_id):
            return chunk.get("asset_object_key")
    return None


@router.get("/{job_id}/chunks/{chunk_id}/preview")
def preview_chunk_pdf(job_id: str, chunk_id: str):
    try:
        pdf_path = find_chunk_preview_pdf(job_id, chunk_id)
        return FileResponse(path=pdf_path, media_type="application/pdf", filename=pdf_path.name, content_disposition_type="inline")
    except FileNotFoundError as exc:
        object_key = _chunk_asset_key(job_id, chunk_id)
        if object_key:
            safe_key = validate_object_key(object_key)
            return _stream_minio_object(get_settings().minio_bucket, safe_key)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.head("/{job_id}/chunks/{chunk_id}/preview")
def head_chunk_pdf(job_id: str, chunk_id: str):
    try:
        pdf_path = find_chunk_preview_pdf(job_id, chunk_id)
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(pdf_path.stat().st_size),
                "Content-Disposition": f'inline; filename="{pdf_path.name}"',
            },
        )
    except FileNotFoundError as exc:
        object_key = _chunk_asset_key(job_id, chunk_id)
        if object_key:
            safe_key = validate_object_key(object_key)
            return asset_head_response(get_settings().minio_bucket, safe_key)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{job_id}/chunks/{chunk_id}/preview-info")
def get_chunk_preview_info(job_id: str, chunk_id: str):
    try:
        chunks = read_chunks(job_id).get("chunks", [])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    chunk = next((item for item in chunks if str(item.get("chunk_id") or item.get("id")) == str(chunk_id)), None)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Chunk not found: {chunk_id}")
    local_available = True
    try:
        find_chunk_preview_pdf(job_id, chunk_id)
    except FileNotFoundError:
        local_available = False
    status_value = "waiting_for_kaggle" if chunk.get("waiting_for_kaggle") else "approved" if chunk.get("approved") else "pending"
    if chunk.get("kaggle_finalized"):
        status_value = "kaggle_finalized"
    if chunk.get("metadata_edu_saved") and chunk.get("minio_uploaded"):
        status_value = "saved"
    return {
        "ok": True,
        "job_id": job_id,
        "chunk_id": chunk_id,
        "chunk_num": chunk.get("chunk_num"),
        "local_preview_available": local_available,
        "backend_preview_url": f"/api/jobs/{job_id}/chunks/{quote(chunk_id, safe='')}/preview",
        "status": status_value,
    }


@router.post("/{job_id}/chunks/add")
def add_chunk(job_id: str, payload: ChunkAddPayload):
    try:
        return add_chunk_for_job(job_id, payload.model_dump(mode="json", exclude_none=True))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{job_id}/chunks/{chunk_id}")
def delete_chunk(job_id: str, chunk_id: str):
    try:
        return delete_chunk_for_job(job_id, chunk_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{job_id}/chunks/recut")
def recut_chunks(job_id: str, payload: ChunkRecutPayload):
    try:
        return recut_chunk(job_id, payload.model_dump(mode="json", exclude_none=True))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{job_id}/chunks/approve")
def approve_chunks(
    job_id: str,
    payload: dict[str, Any] | ChunkListPayload | None = Body(default=None),
):
    try:
        chunks = None
        chunk_ids = None
        if payload is not None:
            if isinstance(payload, ChunkListPayload):
                chunks = [chunk.model_dump(mode="json", exclude_none=True) for chunk in payload.chunks]
            elif isinstance(payload, dict):
                raw_chunks = payload.get("chunks")
                if isinstance(raw_chunks, list):
                    chunks = [dict(chunk) for chunk in raw_chunks if isinstance(chunk, dict)]
                raw_chunk_ids = payload.get("chunk_ids")
                if isinstance(raw_chunk_ids, list):
                    chunk_ids = raw_chunk_ids
        return approve_chunks_for_job(job_id, chunks=chunks, chunk_ids=chunk_ids)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{job_id}/chunks/{chunk_id}/approve")
def approve_chunk(job_id: str, chunk_id: str):
    try:
        return approve_single_chunk_for_job(job_id, chunk_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{job_id}/chunks/finalize-after-kaggle")
def finalize_chunks_after_kaggle(job_id: str, force_without_kaggle: bool = Query(default=False)):
    try:
        return save_final_chunks_after_kaggle(job_id, force_without_kaggle=force_without_kaggle)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{job_id}/chunks/save-metadata-and-sync")
def save_metadata_and_sync_chunks(job_id: str):
    """
    Khi chunks được duyệt (approve): Lưu metadata vào MongoDB + Sync PostgreSQL + Neo4j ngay.
    Không cần chờ Kaggle.
    """
    try:
        return save_chunks_metadata_and_sync(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
