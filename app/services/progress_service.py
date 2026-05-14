from app.core.paths import job_progress_path, job_result_path
from app.models.job_models import JobProgress, JobResult, JobStatus
from app.utils.files import write_json
from app.utils.time import utc_now_iso


def create_initial_progress(job_id: str) -> JobProgress:
    return JobProgress(
        job_id=job_id,
        status=JobStatus.uploaded,
        stage="uploaded",
        percent=0,
        message="Đang chuẩn bị upload sách lên MinIO.",
        current=0,
        total=0,
        updated_at=utc_now_iso(),
    )


def create_initial_result(job_id: str) -> JobResult:
    return JobResult(
        job_id=job_id,
        ok=True,
        status=JobStatus.uploaded,
        message="Job created.",
        data={},
        error=None,
        updated_at=utc_now_iso(),
    )


def write_progress(progress: JobProgress) -> None:
    write_json(job_progress_path(progress.job_id), progress.model_dump(mode="json"))


def write_result(result: JobResult) -> None:
    write_json(job_result_path(result.job_id), result.model_dump(mode="json"))


def update_progress(
    job_id: str,
    *,
    status: JobStatus,
    stage: str,
    message: str,
    percent: int = 0,
    current: int = 0,
    total: int = 0,
    next_available_at: str | None = None,
    recoverable: bool = False,
    retry_stage: str | None = None,
    cooldown_seconds: int | None = None,
) -> JobProgress:
    progress = JobProgress(
        job_id=job_id,
        status=status,
        stage=stage,
        percent=percent,
        message=message,
        current=current,
        total=total,
        updated_at=utc_now_iso(),
        next_available_at=next_available_at,
        recoverable=recoverable,
        retry_stage=retry_stage,
        cooldown_seconds=cooldown_seconds,
    )
    write_progress(progress)
    return progress


def update_result(
    job_id: str,
    *,
    ok: bool,
    status: JobStatus,
    message: str,
    data: dict | None = None,
    error: str | None = None,
) -> JobResult:
    result = JobResult(
        job_id=job_id,
        ok=ok,
        status=status,
        message=message,
        data=data or {},
        error=error,
        updated_at=utc_now_iso(),
    )
    write_result(result)
    return result
