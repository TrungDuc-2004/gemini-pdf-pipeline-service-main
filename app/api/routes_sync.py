from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.models.sync_models import SyncRequest
from app.services.sync_service import sync_metadata

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/metadata")
def sync_all_metadata(payload: SyncRequest):
    try:
        return sync_metadata(
            job_id=None,
            targets=payload.targets,
            create_schema=payload.create_schema,
            rebuild_neo4j=payload.rebuild_neo4j,
            prune_missing=payload.prune_missing,
            enable_embeddings=payload.enable_embeddings,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post("/{job_id}")
def sync_job_metadata(job_id: str, payload: SyncRequest):
    try:
        return sync_metadata(
            job_id=job_id,
            targets=payload.targets,
            create_schema=payload.create_schema,
            rebuild_neo4j=payload.rebuild_neo4j,
            prune_missing=payload.prune_missing,
            enable_embeddings=payload.enable_embeddings,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
