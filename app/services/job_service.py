from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.logging import append_job_log
from app.core.paths import (
    job_config_path,
    job_log_path,
    job_progress_path,
    job_result_path,
    job_source_pdf_path,
    job_state_path,
    job_workspace,
    output_root,
    workspace_root,
)
from app.models.job_models import (
    JobConfig,
    JobCreateResponse,
    JobProgress,
    JobState,
    JobStatus,
    PipelineMode,
)
from app.services.progress_service import (
    create_initial_progress,
    create_initial_result,
    update_progress,
    update_result,
    write_progress,
    write_result,
)
from app.services.subject_upload_service import upload_subject_pdf_for_job
from app.utils.files import atomic_write_json, ensure_dir, read_json, tail_text, write_json
from app.utils.time import utc_now_iso


PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}


def _job_not_found(job_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Job not found: {job_id}",
    )


def validate_pdf_upload(file: UploadFile) -> None:
    filename = file.filename or ""
    content_type = file.content_type or ""
    has_pdf_extension = filename.lower().endswith(".pdf")
    has_pdf_content_type = content_type in PDF_CONTENT_TYPES
    if not has_pdf_extension and not has_pdf_content_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must be a PDF.",
        )


def validate_pipeline_mode(pipeline_mode: str) -> PipelineMode:
    try:
        return PipelineMode(pipeline_mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='pipeline_mode must be "review_first" in Phase 1.',
        ) from exc


async def create_job(
    *,
    file: UploadFile,
    book_name: str,
    class_name: str,
    subject_name: str,
    subject_type: str | None,
    pipeline_mode: str,
    enable_kaggle: bool,
    enable_keywords: bool,
) -> JobCreateResponse:
    validate_pdf_upload(file)
    parsed_pipeline_mode = validate_pipeline_mode(pipeline_mode)

    job_id = str(uuid4())
    now = utc_now_iso()
    workspace = job_workspace(job_id)
    ensure_dir(workspace)
    ensure_dir(workspace / "logs")
    ensure_dir(output_root())

    source_pdf = job_source_pdf_path(job_id)
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded PDF is empty.",
        )
    tmp_source_pdf = source_pdf.with_name(f".{source_pdf.name}.{job_id[:8]}.tmp")
    tmp_source_pdf.write_bytes(content)
    tmp_source_pdf.replace(source_pdf)

    output_path = output_root() / job_id
    config = JobConfig(
        job_id=job_id,
        book_name=book_name,
        class_name=class_name,
        subject_name=subject_name,
        subject_type=subject_type,
        pipeline_mode=parsed_pipeline_mode,
        enable_kaggle=enable_kaggle,
        enable_keywords=enable_keywords,
        source_pdf_path=str(source_pdf),
        created_at=now,
        updated_at=now,
    )
    state = JobState(
        job_id=job_id,
        status=JobStatus.uploaded,
        stage="uploaded",
        book_name=book_name,
        class_name=class_name,
        subject_name=subject_name,
        subject_type=subject_type,
        pipeline_mode=parsed_pipeline_mode,
        source_pdf_path=str(source_pdf),
        workspace_path=str(workspace),
        output_path=str(output_path),
        error=None,
        created_at=now,
        updated_at=now,
    )
    progress = create_initial_progress(job_id)
    result = create_initial_result(job_id)

    write_json(job_config_path(job_id), config.model_dump(mode="json"))
    write_json(job_state_path(job_id), state.model_dump(mode="json"))
    write_progress(progress)
    write_result(result)
    append_job_log(
        job_log_path(job_id),
        f"{now} Job created. status=uploaded source_pdf={source_pdf}",
    )

    try:
        minio_summary = upload_subject_pdf_for_job(
            job_id=job_id,
            source_pdf_path=source_pdf,
            book_name=book_name,
            class_name=class_name,
            subject_name=subject_name,
            subject_type=subject_type,
        )
        state_data = read_json(job_state_path(job_id))
        state_data["stage"] = "uploaded_to_minio"
        state_data["minio"] = minio_summary
        state_data["updated_at"] = utc_now_iso()
        write_json(job_state_path(job_id), state_data)
        update_progress(
            job_id,
            status=JobStatus.uploaded,
            stage="uploaded_to_minio",
            message="Sách đã được tải lên MinIO và sẵn sàng trích xuất chủ đề.",
            percent=100,
            current=1,
            total=1,
        )
        update_result(
            job_id,
            ok=True,
            status=JobStatus.uploaded,
            message="Sách đã được tải lên MinIO và sẵn sàng trích xuất chủ đề.",
            data={"minio": minio_summary},
        )
        append_job_log(
            job_log_path(job_id),
            f"{utc_now_iso()} Subject PDF uploaded to MinIO bucket={minio_summary.get('bucket')} object_key={minio_summary.get('subject_object_key')}",
        )
    except Exception as exc:
        error = f"Không upload được sách lên MinIO/MongoDB: {exc}"
        state_data = read_json(job_state_path(job_id))
        state_data["status"] = JobStatus.error.value
        state_data["stage"] = "uploading_subject_to_minio"
        state_data["error"] = error
        state_data["minio"] = {"enabled": True, "subject_asset_uploaded": False, "error": error}
        state_data["updated_at"] = utc_now_iso()
        write_json(job_state_path(job_id), state_data)
        update_progress(job_id, status=JobStatus.error, stage="uploading_subject_to_minio", message=error, percent=0)
        update_result(job_id, ok=False, status=JobStatus.error, message="Upload MinIO/MongoDB thất bại.", error=error)
        append_job_log(job_log_path(job_id), f"{utc_now_iso()} {error}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error) from exc

    return JobCreateResponse(
        ok=True,
        job_id=job_id,
        status=JobStatus.uploaded,
        workspace_path=str(workspace),
        source_pdf_path=str(source_pdf),
        message="Sách đã được tải lên MinIO và sẵn sàng trích xuất chủ đề.",
        minio=minio_summary,
    )


def ensure_job_exists(job_id: str) -> Path:
    workspace = job_workspace(job_id)
    if not workspace.exists():
        raise _job_not_found(job_id)
    ensure_job_state(job_id)
    return workspace


def _read_json_optional(path: Path) -> dict:
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _topic_review_summary(job_id: str) -> dict:
    topics_path = job_workspace(job_id) / "topics_partial.json"
    approved_path = job_workspace(job_id) / "approved_topics.json"
    topics: list = []
    approved_topics: list = []

    if topics_path.exists():
        raw_topics = read_json(topics_path)
        topics = raw_topics.get("topics", raw_topics) if isinstance(raw_topics, dict) else raw_topics
        if not isinstance(topics, list):
            topics = []

    if approved_path.exists():
        raw_approved = read_json(approved_path)
        if isinstance(raw_approved, dict):
            approved_nums = raw_approved.get("approved_topic_nums") or []
            if approved_nums:
                approved_topics = approved_nums
            else:
                raw_topics = raw_approved.get("topics") or []
                approved_topics = [topic for topic in raw_topics if isinstance(topic, dict) and topic.get("approved")]
        elif isinstance(raw_approved, list):
            approved_topics = [topic for topic in raw_approved if not isinstance(topic, dict) or topic.get("approved")]
        if not isinstance(approved_topics, list):
            approved_topics = []

    topic_count = len(topics)
    return {
        "topics_partial_exists": topics_path.exists(),
        "approved_topics_exists": approved_path.exists(),
        "has_topics": topic_count > 0,
        "topic_count": topic_count,
        "approved_topic_count": len(approved_topics),
        "can_review_topics": topic_count > 0,
    }


def ensure_job_state(job_id: str) -> dict:
    workspace = job_workspace(job_id)
    state_path = job_state_path(job_id)
    if not workspace.exists():
        raise _job_not_found(job_id)
    if state_path.exists():
        state = _read_json_optional(state_path)
        if state.get("job_id"):
            return state

    config = _read_json_optional(job_config_path(job_id))
    progress = _read_json_optional(job_progress_path(job_id))
    result = _read_json_optional(job_result_path(job_id))
    now = utc_now_iso()
    source_pdf = Path(config.get("source_pdf_path") or job_source_pdf_path(job_id))
    status_value = progress.get("status") or result.get("status") or (JobStatus.uploaded.value if source_pdf.exists() else JobStatus.error.value)
    stage_value = progress.get("stage") or status_value
    error = result.get("error")
    if not source_pdf.exists():
        error = error or "job_state.json was missing and source.pdf was not found during recovery."

    recovered_state = {
        "job_id": job_id,
        "status": status_value,
        "stage": stage_value,
        "book_name": config.get("book_name") or result.get("data", {}).get("book_name") or "Recovered job",
        "class_name": config.get("class_name") or "",
        "subject_name": config.get("subject_name") or "",
        "subject_type": config.get("subject_type"),
        "pipeline_mode": config.get("pipeline_mode") or PipelineMode.review_first.value,
        "source_pdf_path": str(source_pdf),
        "workspace_path": str(workspace),
        "output_path": str(output_root() / job_id),
        "error": error,
        "minio": (result.get("data") or {}).get("minio"),
        "recovered": True,
        "recovered_at": now,
        "recovery_reason": "job_state.json was missing or invalid.",
        "created_at": config.get("created_at") or progress.get("updated_at") or result.get("updated_at") or now,
        "updated_at": now,
    }
    atomic_write_json(state_path, recovered_state)
    append_job_log(
        job_log_path(job_id),
        f"{now} WARNING recovered missing job_state.json status={status_value} source_exists={source_pdf.exists()}",
    )
    return recovered_state


def get_job(job_id: str) -> dict:
    ensure_job_exists(job_id)
    state = read_json(job_state_path(job_id))
    state.update(_topic_review_summary(job_id))
    state["paths"] = {
        "workspace_path": str(job_workspace(job_id)),
        "source_pdf_path": str(job_source_pdf_path(job_id)),
        "job_config_path": str(job_config_path(job_id)),
        "job_state_path": str(job_state_path(job_id)),
        "progress_path": str(job_progress_path(job_id)),
        "result_path": str(job_result_path(job_id)),
        "job_log_path": str(job_log_path(job_id)),
    }
    return state


def list_jobs() -> dict:
    root = workspace_root()
    if not root.exists():
        return {"ok": True, "items": [], "count": 0}

    jobs = []
    for job_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        job_id = job_dir.name
        try:
            state = ensure_job_state(job_id)
        except Exception:
            continue
        if not isinstance(state, dict) or not state.get("job_id"):
            continue
        jobs.append(
            {
                "job_id": state.get("job_id"),
                "book_name": state.get("book_name"),
                "class_name": state.get("class_name"),
                "subject_name": state.get("subject_name"),
                "subject_type": state.get("subject_type"),
                "status": state.get("status"),
                "stage": state.get("stage"),
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
                "error": state.get("error"),
                "minio": state.get("minio"),
                **_topic_review_summary(job_id),
            }
        )
    jobs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"ok": True, "items": jobs, "count": len(jobs)}


def get_status(job_id: str) -> dict:
    ensure_job_exists(job_id)
    state = read_json(job_state_path(job_id))
    progress_path = job_progress_path(job_id)
    if progress_path.exists():
        progress = read_json(progress_path)
    else:
        progress = {
            "job_id": job_id,
            "status": state.get("status", JobStatus.error.value),
            "stage": state.get("stage", state.get("status", JobStatus.error.value)),
            "percent": 0,
            "message": state.get("error") or "progress.json was missing; status recovered from job_state.json.",
            "current": 0,
            "total": 0,
            "updated_at": utc_now_iso(),
            "recovered": True,
        }
        write_json(progress_path, progress)
    progress["status"] = state.get("status", progress.get("status"))
    progress["job_status"] = state.get("status")
    progress["state_stage"] = state.get("stage")
    progress["recovered"] = bool(state.get("recovered"))
    progress["recovery_reason"] = state.get("recovery_reason")
    progress.update(_topic_review_summary(job_id))
    return progress


def debug_job_files(job_id: str) -> dict:
    ensure_job_exists(job_id)
    workspace = job_workspace(job_id)
    names = [
        "job_state.json",
        "job_config.json",
        "progress.json",
        "result.json",
        "source.pdf",
        "logs/job.log",
        "logs/topics.log",
        "topics_partial.json",
        "extraction_state.json",
    ]
    files = {}
    for name in names:
        path = workspace / name
        files[name] = {
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "path": str(path),
        }
    return {"ok": True, "job_id": job_id, "workspace_path": str(workspace), "files": files}


def get_logs(job_id: str, lines: int = 100) -> dict:
    ensure_job_exists(job_id)
    return {
        "job_id": job_id,
        "lines": lines,
        "log": tail_text(job_log_path(job_id), lines=lines),
    }


def update_job_state(
    job_id: str,
    *,
    status: JobStatus,
    stage: str | None = None,
    error: str | None = None,
) -> dict:
    ensure_job_exists(job_id)
    state = read_json(job_state_path(job_id))
    state["status"] = status.value if hasattr(status, "value") else str(status)
    state["stage"] = stage or state.get("stage") or state["status"]
    state["error"] = error
    state["updated_at"] = utc_now_iso()
    write_json(job_state_path(job_id), state)
    return state
