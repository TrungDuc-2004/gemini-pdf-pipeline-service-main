from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.gemini_keys import GeminiKeyManager
from app.core.logging import append_job_log
from app.core.paths import job_log_path, job_workspace
from app.pipeline.gemini_runner import extract_structure_from_pdf
from app.services.progress_service import update_progress
from app.models.job_models import JobStatus
from app.utils.files import read_json, write_json
from app.utils.time import utc_now_iso


def _keyword_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "keyword.log"


def _log(job_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}"
    append_job_log(_keyword_log_path(job_id), line)
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [keywords] {message}")


def build_keyword_prompt(num_keywords: int) -> str:
    return f"""
Bạn là trợ lý trích xuất dữ liệu cho luận văn: bóc tách SGK Tin học THPT (tiếng Việt).
Nhiệm vụ: trích xuất từ khóa quan trọng nhất từ NỘI DUNG trong file PDF được cung cấp (đây là 1 CHUNK của bài học).

YÊU CẦU:
- Trả về đúng {num_keywords} từ khóa (hoặc ít hơn nếu nội dung quá ngắn, nhưng cố gắng đủ).
- Mỗi từ khóa: 1-4 từ, tiếng Việt có dấu nếu cần.
- Ưu tiên: khái niệm Tin học, thuật ngữ, công cụ, thao tác/quy trình, cấu trúc dữ liệu, thuật toán, cú pháp, thành phần hệ thống.
- Loại bỏ từ chung chung: "bài học", "học sinh", "câu hỏi", "hoạt động", "thực hành", "hình", "bảng", "ví dụ".
- Không trùng lặp (không lặp cùng nghĩa chỉ khác viết hoa).
- Chỉ trả về JSON, KHÔNG giải thích, KHÔNG markdown.

OUTPUT JSON SCHEMA (bắt buộc):
{{
  "keywords": [
    {{"keyword": "..." }},
    {{"keyword": "..." }}
  ]
}}
""".strip()


@dataclass
class KeywordBatchSummary:
    total_lessons: int = 0
    total_chunks: int = 0
    skipped_existing_success: int = 0
    retried_empty: int = 0
    retried_error: int = 0
    succeeded: int = 0
    failed: int = 0
    pending: int = 0
    lesson_type_written: int = 0
    failed_chunks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_lessons": self.total_lessons,
            "total_chunks": self.total_chunks,
            "skipped_existing_success": self.skipped_existing_success,
            "retried_empty": self.retried_empty,
            "retried_error": self.retried_error,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "pending": self.pending,
            # Backward-compatible aliases used by the first Phase 9 run.
            "extracted": self.succeeded,
            "skipped": self.skipped_existing_success,
            "lesson_type_written": self.lesson_type_written,
            "failed_chunks": self.failed_chunks,
        }


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = read_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    write_json(path, data)


def _has_nonempty_keywords(path: Path) -> bool:
    data = _safe_load_json(path)
    if not isinstance(data, dict):
        return False
    keywords = data.get("keywords")
    return isinstance(keywords, list) and len(keywords) > 0


def _keyword_file_state(path: Path) -> str:
    data = _safe_load_json(path)
    if not isinstance(data, dict):
        return "missing"
    keywords = data.get("keywords")
    if data.get("error"):
        return "error"
    if isinstance(keywords, list) and keywords:
        return "success"
    return "empty"


def _find_chunk_pdf(chunk_dir: Path) -> Path | None:
    pdfs = sorted([path for path in chunk_dir.glob("*.pdf") if path.is_file()])
    if not pdfs:
        return None
    for path in pdfs:
        if "_chunk_" in path.name:
            return path
    return pdfs[0]


def _chunk_dirs_of_lesson(lesson_dir: Path) -> list[Path]:
    if not lesson_dir.exists():
        return []
    return sorted(
        [path for path in lesson_dir.iterdir() if path.is_dir() and path.name.startswith("chunk_")],
        key=lambda path: path.name,
    )


def _extract_lesson_id(lesson_stem: str) -> str | None:
    match = re.search(r"(lesson_\d+)", lesson_stem)
    return match.group(1) if match else None


def _find_lesson_json(book_dir: Path, lesson_stem: str) -> Path | None:
    book_stem = book_dir.name
    lesson_id = _extract_lesson_id(lesson_stem)
    if not lesson_id:
        return None
    lesson_folder = book_dir / "Lesson" / lesson_id
    if not lesson_folder.exists():
        return None
    expected = lesson_folder / f"{book_stem}_{lesson_id}.json"
    if expected.exists():
        return expected
    candidates = sorted(lesson_folder.glob("*.json"))
    for path in candidates:
        if lesson_id in path.stem:
            return path
    return candidates[0] if candidates else None


def infer_lesson_type(chunk_dirs: list[Path]) -> str:
    return "thuc hanh" if len(chunk_dirs) == 1 else "ly thuyet"


def num_keywords_for_lesson_type(lesson_type: str) -> int:
    return 10 if lesson_type == "thuc hanh" else 5


def _update_json_file_fields(path: Path, fields: dict[str, Any]) -> bool:
    data = _safe_load_json(path) or {}
    before = {key: data.get(key) for key in fields}
    data.update(fields)
    after = {key: data.get(key) for key in fields}
    changed = before != after
    if changed:
        _write_json_file(path, data)
    return changed


def update_lesson_level_json(book_dir: Path, lesson_stem: str, lesson_type: str, chunk_count: int) -> Path | None:
    path = _find_lesson_json(book_dir, lesson_stem)
    if path is None:
        return None
    _update_json_file_fields(path, {"lesson_type": lesson_type, "chunk_count": chunk_count})
    return path


def _update_lesson_type_meta(lesson_dir: Path, lesson_type: str, chunk_count: int) -> bool:
    changed = False
    for chunk_dir in _chunk_dirs_of_lesson(lesson_dir):
        chunk_pdf = _find_chunk_pdf(chunk_dir)
        if chunk_pdf is None:
            continue
        meta_path = chunk_pdf.with_suffix(".json")
        if meta_path.exists():
            changed = _update_json_file_fields(
                meta_path,
                {"lesson_type": lesson_type, "chunk_count": chunk_count},
            ) or changed
    fallback = lesson_dir / "lesson_meta.json"
    changed = _update_json_file_fields(
        fallback,
        {"lesson_type": lesson_type, "chunk_count": chunk_count},
    ) or changed
    return changed


def normalize_keyword_output(data: Any) -> dict[str, Any]:
    output = data if isinstance(data, dict) else {}
    raw_keywords = output.get("keywords", [])
    normalized: list[dict[str, str]] = []
    if isinstance(raw_keywords, list):
        for item in raw_keywords:
            if isinstance(item, dict):
                keyword = item.get("keyword")
            else:
                keyword = item
            if isinstance(keyword, str) and keyword.strip():
                normalized.append({"keyword": keyword.strip()})

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for item in normalized:
        key = item["keyword"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return {"keywords": deduped}


def extract_keywords_from_chunk_pdf(
    *,
    key_manager: GeminiKeyManager,
    chunk_pdf_path: str,
    model: str,
    num_keywords: int,
    status_cb=None,
) -> dict[str, Any]:
    data = extract_structure_from_pdf(
        key_manager=key_manager,
        pdf_path=chunk_pdf_path,
        prompt=build_keyword_prompt(num_keywords),
        model=model,
        status_cb=status_cb,
    )
    result = normalize_keyword_output(data)
    result["keywords"] = result["keywords"][:num_keywords]
    return result


def ensure_keyword_placeholders(book_dir: Path) -> int:
    count = 0
    for chunk_pdf in sorted((book_dir / "Chunk").rglob("*.pdf")):
        keyword_path = chunk_pdf.with_suffix(".keywords.json")
        if not keyword_path.exists():
            _write_json_file(keyword_path, {"keywords": []})
        count += 1
    return count


def extract_keywords_for_book(
    *,
    job_id: str,
    book_dir: Path,
    force_reprocess: bool = False,
) -> KeywordBatchSummary:
    chunk_root = book_dir / "Chunk"
    if not chunk_root.exists():
        raise FileNotFoundError(f"Chunk root not found: {chunk_root}")

    key_manager = GeminiKeyManager.from_env()
    if key_manager.key_count() == 0:
        raise RuntimeError("No Gemini API keys configured. Set GEMINI_API_KEYS or GEMINI_API_KEY_1.")

    summary = KeywordBatchSummary()
    lesson_dirs = sorted([path for path in chunk_root.iterdir() if path.is_dir()])
    summary.total_lessons = len(lesson_dirs)
    total_chunks = sum(len(_chunk_dirs_of_lesson(lesson_dir)) for lesson_dir in lesson_dirs)
    summary.total_chunks = total_chunks
    summary.pending = total_chunks

    _log(job_id, f"keyword extraction started lessons={summary.total_lessons} chunks={total_chunks}")
    processed = 0

    def write_summary() -> None:
        write_json(job_workspace(job_id) / "keyword_summary.json", summary.to_dict())

    write_summary()
    for lesson_index, lesson_dir in enumerate(lesson_dirs, 1):
        chunk_dirs = _chunk_dirs_of_lesson(lesson_dir)
        if not chunk_dirs:
            continue
        lesson_type = infer_lesson_type(chunk_dirs)
        keyword_count = num_keywords_for_lesson_type(lesson_type)
        lesson_json = update_lesson_level_json(book_dir, lesson_dir.name, lesson_type, len(chunk_dirs))
        meta_changed = _update_lesson_type_meta(lesson_dir, lesson_type, len(chunk_dirs))
        if lesson_json or meta_changed:
            summary.lesson_type_written += 1

        _log(
            job_id,
            f"lesson {lesson_index}/{summary.total_lessons}: {lesson_dir.name} chunks={len(chunk_dirs)} type={lesson_type}",
        )
        for chunk_index, chunk_dir in enumerate(chunk_dirs, 1):
            chunk_pdf = _find_chunk_pdf(chunk_dir)
            if chunk_pdf is None:
                continue
            processed += 1
            keyword_path = chunk_pdf.with_suffix(".keywords.json")
            keyword_state = _keyword_file_state(keyword_path)
            if not force_reprocess and keyword_state == "success":
                summary.skipped_existing_success += 1
                summary.pending = max(0, total_chunks - summary.skipped_existing_success - summary.succeeded - summary.failed)
                write_summary()
                continue
            if keyword_state == "error":
                summary.retried_error += 1
            elif keyword_state in {"missing", "empty"}:
                summary.retried_empty += 1

            update_progress(
                job_id,
                status=JobStatus.extracting_keywords,
                stage="extracting_keywords",
                message=f"Extracting keywords {processed}/{total_chunks}: {chunk_pdf.name}",
                percent=round(processed * 100 / total_chunks) if total_chunks else 0,
                current=processed,
                total=total_chunks,
            )
            _log(job_id, f"processing {lesson_dir.name}/{chunk_dir.name} ({chunk_index}/{len(chunk_dirs)})")
            try:
                result = extract_keywords_from_chunk_pdf(
                    key_manager=key_manager,
                    chunk_pdf_path=str(chunk_pdf),
                    model=get_settings().gemini_model,
                    num_keywords=keyword_count,
                    status_cb=lambda message: _log(job_id, f"gemini: {message}"),
                )
                _write_json_file(keyword_path, result)
                summary.succeeded += 1
                summary.pending = max(0, total_chunks - summary.skipped_existing_success - summary.succeeded - summary.failed)
                write_summary()
                _log(job_id, f"ok {lesson_dir.name}/{chunk_dir.name} keywords={len(result.get('keywords', []))}")
            except Exception as exc:
                summary.failed += 1
                summary.pending = max(0, total_chunks - summary.skipped_existing_success - summary.succeeded - summary.failed)
                failure = {
                    "lesson_stem": lesson_dir.name,
                    "chunk_dir": chunk_dir.name,
                    "chunk_pdf": str(chunk_pdf),
                    "error": str(exc),
                }
                summary.failed_chunks.append(failure)
                _write_json_file(keyword_path, {"keywords": [], "error": str(exc)})
                write_summary()
                _log(job_id, f"failed {lesson_dir.name}/{chunk_dir.name}: {exc}")
                raise RuntimeError(f"Keyword extraction failed for {chunk_pdf}: {exc}") from exc

    summary.pending = 0
    write_summary()
    _log(
        job_id,
        "keyword extraction completed "
        f"succeeded={summary.succeeded} skipped_existing_success={summary.skipped_existing_success} "
        f"retried_empty={summary.retried_empty} retried_error={summary.retried_error}",
    )
    return summary
