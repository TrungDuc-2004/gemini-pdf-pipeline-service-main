from fastapi import APIRouter, Query

from app.services.job_service import get_logs

router = APIRouter(prefix="/api/jobs", tags=["logs"])


@router.get("/{job_id}/logs")
def read_job_logs(job_id: str, lines: int = Query(default=100, ge=1, le=5000)):
    return get_logs(job_id, lines=lines)

