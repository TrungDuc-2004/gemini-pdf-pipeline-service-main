from pydantic import BaseModel, Field


class BundleCounts(BaseModel):
    topics: int = 0
    lessons: int = 0
    chunks: int = 0
    keyword_files: int = 0
    topic_pdfs: int = 0
    lesson_pdfs: int = 0
    chunk_pdfs: int = 0


class BundleSummary(BaseModel):
    ok: bool = True
    job_id: str
    status: str
    book_stem: str
    bundle_path: str
    manifest_path: str
    counts: BundleCounts
    missing: list[str] = Field(default_factory=list)
