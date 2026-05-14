from typing import Any

from pydantic import BaseModel, Field


class MongoImportCounts(BaseModel):
    class_count: int = 0
    subject_count: int = 0
    topic_count: int = 0
    lesson_count: int = 0
    chunk_count: int = 0
    keyword_count: int = 0
    chunk_keyword_count: int = 0
    skipped_keyword_files: int = 0
    error_keyword_files: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    upserted_count: int = 0


class MongoImportResult(BaseModel):
    ok: bool
    job_id: str
    status: str
    book_stem: str
    bundle_path: str
    db_name: str
    counts: MongoImportCounts
    errors: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str
    completed_at: str | None = None
