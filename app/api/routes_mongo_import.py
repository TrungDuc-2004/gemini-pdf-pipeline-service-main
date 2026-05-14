from fastapi import APIRouter, HTTPException, Query, status

from app.services.metadata_edu_import_service import import_bundle_to_metadata_edu, read_mongo_import_result

router = APIRouter(prefix="/api/jobs", tags=["mongo-import"])


@router.post("/{job_id}/import-mongodb")
def import_mongodb(
    job_id: str,
    upload_minio: bool = Query(True),
    dry_run: bool = Query(False),
):
    try:
        return import_bundle_to_metadata_edu(job_id, upload_minio=upload_minio, dry_run=dry_run)
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/{job_id}/mongo-import-result")
def get_mongo_import_result(job_id: str):
    try:
        return read_mongo_import_result(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
