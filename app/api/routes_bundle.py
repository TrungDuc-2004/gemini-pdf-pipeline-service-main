from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.models.job_models import JobStatus
from app.services.bundle_service import (
    BUNDLE_READABLE_STATUSES,
    build_bundle_summary,
    create_bundle_zip,
    ensure_bundle_preconditions,
    prepare_bundle_for_job,
)
from app.services.job_service import ensure_job_exists, get_status, update_job_state
from app.services.progress_service import update_progress

router = APIRouter(prefix="/api/jobs", tags=["bundle"])


@router.post("/{job_id}/prepare-bundle")
def prepare_bundle(
    job_id: str,
    background_tasks: BackgroundTasks,
    skip_kaggle: bool = Query(default=False),
    skip_keywords: bool = Query(default=False),
    retry_failed_keywords_only: bool = Query(default=False),
):
    try:
        ensure_bundle_preconditions(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    update_job_state(job_id, status=JobStatus.preparing_bundle, stage="preparing_bundle")
    update_progress(
        job_id,
        status=JobStatus.preparing_bundle,
        stage="preparing_bundle",
        message="Đang tạo bundle cuối, vui lòng chờ...",
        percent=5,
    )
    background_tasks.add_task(
        prepare_bundle_for_job,
        job_id,
        skip_kaggle=skip_kaggle,
        skip_keywords=skip_keywords,
        retry_failed_keywords_only=retry_failed_keywords_only,
    )
    return {
        "ok": True,
        "job_id": job_id,
        "status": JobStatus.preparing_bundle,
        "message": "Đang tạo bundle cuối, vui lòng chờ...",
        "skip_kaggle": skip_kaggle,
        "skip_keywords": skip_keywords,
        "retry_failed_keywords_only": retry_failed_keywords_only,
    }


@router.get("/{job_id}/bundle")
def get_bundle(job_id: str):
    try:
        ensure_job_exists(job_id)
        status_payload = get_status(job_id)
        if status_payload.get("status") not in BUNDLE_READABLE_STATUSES:
            return {
                "ok": False,
                "job_id": job_id,
                "status": status_payload.get("status"),
                "message": "Bundle is not ready.",
                "progress": status_payload,
            }
        return build_bundle_summary(job_id, require_ready=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{job_id}/bundle/download")
def download_bundle(job_id: str):
    try:
        zip_path = create_bundle_zip(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=zip_path.name,
    )
