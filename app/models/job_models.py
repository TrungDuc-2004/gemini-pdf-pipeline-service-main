from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    uploaded = "uploaded"
    waiting_gemini_cooldown = "waiting_gemini_cooldown"
    extracting_topics = "extracting_topics"
    reviewing_topics = "reviewing_topics"
    extracting_lessons = "extracting_lessons"
    reviewing_lessons = "reviewing_lessons"
    extracting_chunks = "extracting_chunks"
    reviewing_chunks = "reviewing_chunks"
    preparing_bundle = "preparing_bundle"
    running_kaggle = "running_kaggle"
    extracting_keywords = "extracting_keywords"
    bundle_ready = "bundle_ready"
    importing_mongodb = "importing_mongodb"
    mongodb_imported = "mongodb_imported"
    error = "error"


class PipelineMode(StrEnum):
    review_first = "review_first"


class JobConfig(BaseModel):
    job_id: str
    book_name: str
    class_name: str
    subject_name: str
    subject_type: str | None = None
    pipeline_mode: PipelineMode = PipelineMode.review_first
    enable_kaggle: bool = False
    enable_keywords: bool = True
    source_pdf_path: str
    created_at: str
    updated_at: str


class JobState(BaseModel):
    job_id: str
    status: JobStatus
    stage: str
    book_name: str
    class_name: str
    subject_name: str
    subject_type: str | None = None
    pipeline_mode: PipelineMode
    source_pdf_path: str
    workspace_path: str
    output_path: str
    error: str | None = None
    minio: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class JobProgress(BaseModel):
    job_id: str
    status: JobStatus
    stage: str
    percent: int = Field(ge=0, le=100)
    message: str
    current: int
    total: int
    updated_at: str
    next_available_at: str | None = None
    recoverable: bool = False
    retry_stage: str | None = None
    cooldown_seconds: int | None = None


class JobResult(BaseModel):
    job_id: str
    ok: bool
    status: JobStatus
    message: str
    data: dict[str, Any]
    error: str | None = None
    updated_at: str


class JobCreateResponse(BaseModel):
    ok: bool
    job_id: str
    status: JobStatus
    workspace_path: str
    source_pdf_path: str
    message: str
    minio: dict[str, Any] | None = None


class JobLogResponse(BaseModel):
    job_id: str
    lines: int
    log: str


class FutureEndpointResponse(BaseModel):
    ok: bool = False
    message: str = "Not implemented in Phase 1"
    phase: str = "future"
