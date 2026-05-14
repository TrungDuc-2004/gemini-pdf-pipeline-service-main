from __future__ import annotations

import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile

load_dotenv()

PIPELINE_SERVICE_URL = os.getenv("PIPELINE_SERVICE_URL", "http://localhost:8100").rstrip("/")
DEFAULT_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 5
MAX_STAGE_WAIT_SECONDS = 30 * 60

app = FastAPI(
    title="fake-backend",
    version="0.1.0",
    description="Demo backend that integrates with gemini-pdf-pipeline-service over HTTP.",
)


def _pipeline_url(path: str) -> str:
    return f"{PIPELINE_SERVICE_URL}{path}"


def _raise_pipeline_error(exc: httpx.RequestError) -> None:
    raise HTTPException(
        status_code=502,
        detail={
            "message": "Pipeline service is not reachable.",
            "pipeline_service_url": PIPELINE_SERVICE_URL,
            "error": str(exc),
        },
    ) from exc


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(
                method,
                _pipeline_url(path),
                params=params,
                json=json,
                files=files,
                data=data,
            )
    except httpx.RequestError as exc:
        _raise_pipeline_error(exc)

    if response.status_code >= 400:
        try:
            body: Any = response.json()
        except Exception:
            body = response.text
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "message": "Pipeline service returned an error.",
                "pipeline_status_code": response.status_code,
                "pipeline_response": body,
            },
        )

    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


def _get(path: str, **kwargs: Any) -> Any:
    return _request("GET", path, **kwargs)


def _post(path: str, **kwargs: Any) -> Any:
    return _request("POST", path, **kwargs)


def _wait_for_status(job_id: str, target_statuses: set[str], stage_name: str) -> dict[str, Any]:
    deadline = time.monotonic() + MAX_STAGE_WAIT_SECONDS
    last_status: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        status_payload = _get(f"/api/jobs/{job_id}/status")
        last_status = status_payload
        status = status_payload.get("status")
        if status in target_statuses:
            return status_payload
        if status == "error":
            raise HTTPException(
                status_code=502,
                detail={
                    "message": f"Pipeline stage failed: {stage_name}",
                    "stage": stage_name,
                    "pipeline_status": status_payload,
                },
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    raise HTTPException(
        status_code=504,
        detail={
            "message": f"Timed out waiting for pipeline stage: {stage_name}",
            "stage": stage_name,
            "last_status": last_status,
        },
    )


def _approve_current_topics(job_id: str) -> Any:
    topics = _get(f"/api/jobs/{job_id}/topics")
    return _post(f"/api/jobs/{job_id}/topics/approve", json={"topics": topics.get("topics", [])})


def _approve_current_lessons(job_id: str) -> Any:
    lessons = _get(f"/api/jobs/{job_id}/lessons")
    return _post(f"/api/jobs/{job_id}/lessons/approve", json={"lessons": lessons.get("lessons", [])})


def _approve_current_chunks(job_id: str) -> Any:
    chunks = _get(f"/api/jobs/{job_id}/chunks")
    return _post(f"/api/jobs/{job_id}/chunks/approve", json={"chunks": chunks.get("chunks", [])})


def _run_until_bundle_ready_no_review(job_id: str, *, skip_keywords: bool = True) -> dict[str, Any]:
    summary: dict[str, Any] = {"job_id": job_id, "steps": []}

    _post(f"/api/jobs/{job_id}/extract/topics")
    summary["steps"].append({"stage": "extract_topics", "status": "started"})
    summary["topics_status"] = _wait_for_status(job_id, {"reviewing_topics"}, "extract_topics")
    summary["approve_topics"] = _approve_current_topics(job_id)

    _post(f"/api/jobs/{job_id}/extract/lessons")
    summary["steps"].append({"stage": "extract_lessons", "status": "started"})
    summary["lessons_status"] = _wait_for_status(job_id, {"reviewing_lessons"}, "extract_lessons")
    summary["approve_lessons"] = _approve_current_lessons(job_id)

    _post(f"/api/jobs/{job_id}/extract/chunks")
    summary["steps"].append({"stage": "extract_chunks", "status": "started"})
    summary["chunks_status"] = _wait_for_status(job_id, {"reviewing_chunks"}, "extract_chunks")
    summary["approve_chunks"] = _approve_current_chunks(job_id)

    _post(
        f"/api/jobs/{job_id}/prepare-bundle",
        params={"skip_kaggle": "true", "skip_keywords": str(skip_keywords).lower()},
    )
    summary["steps"].append(
        {
            "stage": "prepare_bundle",
            "status": "started",
            "skip_kaggle": True,
            "skip_keywords": skip_keywords,
        }
    )
    summary["bundle_status"] = _wait_for_status(job_id, {"bundle_ready"}, "prepare_bundle")
    summary["bundle"] = _get(f"/api/jobs/{job_id}/bundle")
    return summary


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "fake-backend",
        "pipeline_service_url": PIPELINE_SERVICE_URL,
    }


@app.post("/demo/jobs")
async def create_demo_job(
    file: UploadFile = File(...),
    book_name: str = Form(...),
    class_name: str = Form(...),
    subject_name: str = Form(...),
    subject_type: str | None = Form(default=None),
    enable_keywords: bool = Form(default=True),
    enable_kaggle: bool = Form(default=False),
):
    content = await file.read()
    files = {
        "file": (
            file.filename or "source.pdf",
            content,
            file.content_type or "application/pdf",
        )
    }
    data = {
        "book_name": book_name,
        "class_name": class_name,
        "subject_name": subject_name,
        "subject_type": subject_type or "",
        "pipeline_mode": "review_first",
        "enable_keywords": str(enable_keywords).lower(),
        "enable_kaggle": str(enable_kaggle).lower(),
    }
    return _post("/api/jobs", files=files, data=data, timeout=120)


@app.get("/demo/jobs/{job_id}/status")
def demo_status(job_id: str):
    return _get(f"/api/jobs/{job_id}/status")


@app.get("/demo/jobs/{job_id}/logs")
def demo_logs(job_id: str, lines: int = Query(default=100)):
    return _get(f"/api/jobs/{job_id}/logs", params={"lines": lines})


@app.post("/demo/jobs/{job_id}/extract-topics")
def demo_extract_topics(job_id: str):
    return _post(f"/api/jobs/{job_id}/extract/topics")


@app.get("/demo/jobs/{job_id}/topics")
def demo_topics(job_id: str):
    return _get(f"/api/jobs/{job_id}/topics")


@app.post("/demo/jobs/{job_id}/approve-topics")
def demo_approve_topics(job_id: str):
    return _approve_current_topics(job_id)


@app.post("/demo/jobs/{job_id}/extract-lessons")
def demo_extract_lessons(job_id: str):
    return _post(f"/api/jobs/{job_id}/extract/lessons")


@app.get("/demo/jobs/{job_id}/lessons")
def demo_lessons(job_id: str):
    return _get(f"/api/jobs/{job_id}/lessons")


@app.post("/demo/jobs/{job_id}/approve-lessons")
def demo_approve_lessons(job_id: str):
    return _approve_current_lessons(job_id)


@app.post("/demo/jobs/{job_id}/extract-chunks")
def demo_extract_chunks(job_id: str):
    return _post(f"/api/jobs/{job_id}/extract/chunks")


@app.get("/demo/jobs/{job_id}/chunks")
def demo_chunks(job_id: str):
    return _get(f"/api/jobs/{job_id}/chunks")


@app.post("/demo/jobs/{job_id}/approve-chunks")
def demo_approve_chunks(job_id: str):
    return _approve_current_chunks(job_id)


@app.post("/demo/jobs/{job_id}/prepare-bundle")
def demo_prepare_bundle(
    job_id: str,
    skip_kaggle: bool = Query(default=False),
    skip_keywords: bool = Query(default=False),
    retry_failed_keywords_only: bool = Query(default=False),
):
    return _post(
        f"/api/jobs/{job_id}/prepare-bundle",
        params={
            "skip_kaggle": str(skip_kaggle).lower(),
            "skip_keywords": str(skip_keywords).lower(),
            "retry_failed_keywords_only": str(retry_failed_keywords_only).lower(),
        },
    )


@app.get("/demo/jobs/{job_id}/bundle")
def demo_bundle(job_id: str):
    return _get(f"/api/jobs/{job_id}/bundle")


@app.post("/demo/jobs/{job_id}/import-mongodb")
def demo_import_mongodb(job_id: str):
    return _post(f"/api/jobs/{job_id}/import-mongodb", timeout=120)


@app.get("/demo/jobs/{job_id}/mongo-import-result")
def demo_mongo_import_result(job_id: str):
    return _get(f"/api/jobs/{job_id}/mongo-import-result")


@app.post("/demo/jobs/{job_id}/run-until-bundle-ready-no-review")
def demo_run_until_bundle_ready_no_review(
    job_id: str,
    skip_keywords: bool = Query(default=True),
):
    return _run_until_bundle_ready_no_review(job_id, skip_keywords=skip_keywords)


@app.post("/demo/upload-and-run-no-review")
async def demo_upload_and_run_no_review(
    file: UploadFile = File(...),
    book_name: str = Form(...),
    class_name: str = Form(...),
    subject_name: str = Form(...),
    subject_type: str | None = Form(default=None),
    enable_keywords: bool = Form(default=True),
    enable_kaggle: bool = Form(default=False),
    import_mongodb: bool = Query(default=False),
):
    created = await create_demo_job(
        file=file,
        book_name=book_name,
        class_name=class_name,
        subject_name=subject_name,
        subject_type=subject_type,
        enable_keywords=enable_keywords,
        enable_kaggle=enable_kaggle,
    )
    job_id = created["job_id"]
    summary = _run_until_bundle_ready_no_review(job_id, skip_keywords=True)
    if import_mongodb:
        summary["mongo_import"] = _post(f"/api/jobs/{job_id}/import-mongodb", timeout=120)
    return {"ok": True, "created": created, "summary": summary}
