from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.core.config import get_settings
from app.core.gemini_keys import GeminiKeyManager
from app.core.logging import append_job_log
from app.core.paths import job_config_path, job_log_path, job_workspace, output_root
from app.models.job_models import JobStatus
from app.pipeline.chunk_pipeline import (
    _compute_chunks_from_start_head,
    _extract_page_text,
    _flatten_start_head,
    _heading_valid_in_page,
    _is_junk_candidate,
    rebuild_lesson_chunks,
    run_extract_and_split_chunks_for_book,
)
from app.pipeline.gemini_runner import extract_structure_from_pdf
from app.pipeline.prompts import build_chunk_prompt_start_head
from app.services.gemini_cooldown_service import is_all_keys_cooldown_error, mark_waiting_for_gemini_cooldown
from app.services.job_service import ensure_job_exists, update_job_state
from app.services.lesson_service import _build_lesson_pdfs, _build_topic_pdfs, _write_bundle_manifest
from app.services.progress_service import update_progress, update_result
from app.utils.files import read_json, write_json
from app.utils.time import utc_now_iso


def _workspace_file(job_id: str, name: str) -> Path:
    return job_workspace(job_id) / name


def _chunk_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "chunks.log"


def _log(job_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}"
    append_job_log(_chunk_log_path(job_id), line)
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [chunks] {message}")


def _extract_items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get(key, payload.get("items", []))
    else:
        items = payload
    return [dict(item) for item in items or [] if isinstance(item, dict)]


def _chunk_number(value: Any, fallback: int) -> str:
    if value is None:
        return str(fallback)
    text = str(value)
    match = re.search(r"\d+", text)
    return match.group(0) if match else str(fallback)


def _find_bundle(job_id: str) -> tuple[dict[str, Any], str, Path]:
    state_path = _workspace_file(job_id, "extraction_state.json")
    if not state_path.exists():
        raise FileNotFoundError("extraction_state.json not found. Topic/lesson stages must run first.")
    state = read_json(state_path)
    book_stem = state.get("book_stem")
    if not book_stem:
        raise FileNotFoundError("book_stem missing in extraction_state.json.")
    bundle_dir = Path(state.get("bundle_path") or state.get("rebuilt_bundle_path") or job_workspace(job_id) / book_stem)
    return state, book_stem, bundle_dir


def _lesson_pdf_map(bundle_dir: Path) -> dict[str, Path]:
    return {pdf.stem: pdf for pdf in sorted((bundle_dir / "Lesson").rglob("*.pdf"))}


def _normalize_chunk(meta: dict[str, Any], index: int, lesson_lookup: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    lesson_stem = str(meta.get("lesson_stem") or "")
    chunk = str(meta.get("chunk") or meta.get("chunk_num") or f"chunk_{index + 1:02d}")
    if not chunk.startswith("chunk_"):
        chunk = f"chunk_{int(_chunk_number(chunk, index + 1)):02d}"
    lesson_info = (lesson_lookup or {}).get(lesson_stem, {})
    chunk_num = _chunk_number(meta.get("chunk_num") or chunk, index + 1)
    title = meta.get("title") or meta.get("chunk_name") or ""
    pdf_path = meta.get("pdf_path") or meta.get("chunk_pdf")
    metadata_path = meta.get("metadata_path")
    out = {
        **meta,
        "chunk_id": meta.get("chunk_id") or meta.get("id") or f"{lesson_stem}:{chunk}",
        "id": meta.get("id") or meta.get("chunk_id") or f"{lesson_stem}:{chunk}",
        "lesson_stem": lesson_stem,
        "topic_num": meta.get("topic_num") or lesson_info.get("topic_num", ""),
        "topic_name": meta.get("topic_name") or lesson_info.get("topic_name", ""),
        "lesson_num": meta.get("lesson_num") or lesson_info.get("lesson_num", ""),
        "lesson_name": meta.get("lesson_name") or lesson_info.get("lesson_name", ""),
        "chunk": chunk,
        "chunk_num": chunk_num,
        "chunk_name": meta.get("chunk_name") or title or chunk,
        "heading": meta.get("heading") or "",
        "title": title,
        "start": int(meta.get("start") or 1),
        "end": int(meta.get("end") or meta.get("start") or 1),
        "content_head": bool(meta.get("content_head", False)),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "chunk_pdf": str(pdf_path) if pdf_path else meta.get("chunk_pdf"),
        "metadata_path": str(metadata_path) if metadata_path else meta.get("metadata_path"),
    }
    return out


def _lesson_lookup(job_id: str, bundle_dir: Path) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    approved_path = _workspace_file(job_id, "approved_lessons.json")
    lessons = _extract_items(read_json(approved_path), "lessons") if approved_path.exists() else []
    pdfs = _lesson_pdf_map(bundle_dir)
    for lesson in lessons:
        name = lesson.get("name")
        stem = ""
        if name:
            matches = [pdf_stem for pdf_stem in pdfs if pdf_stem.endswith(str(name))]
            stem = matches[0] if matches else ""
        if stem:
            lookup[stem] = lesson
    return lookup


def _collect_chunk_metas(bundle_dir: Path, job_id: str) -> list[dict[str, Any]]:
    lookup = _lesson_lookup(job_id, bundle_dir)
    chunks = []
    for meta_path in sorted((bundle_dir / "Chunk").rglob("*.json")):
        if meta_path.name.endswith(".keywords.json"):
            continue
        try:
            meta = read_json(meta_path)
        except Exception:
            continue
        if not meta.get("lesson_stem") or not meta.get("chunk"):
            continue
        meta["metadata_path"] = str(meta_path)
        chunks.append(_normalize_chunk(meta, len(chunks), lookup))
    chunks.sort(key=lambda item: (item.get("lesson_stem", ""), item.get("chunk", "")))
    return chunks


def _group_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    index_by_stem: dict[str, int] = {}
    for chunk in chunks:
        stem = str(chunk.get("lesson_stem") or "")
        if stem not in index_by_stem:
            index_by_stem[stem] = len(groups)
            groups.append(
                {
                    "lesson_num": str(chunk.get("lesson_num") or ""),
                    "lesson_name": str(chunk.get("lesson_name") or ""),
                    "lesson_stem": stem,
                    "chunks": [],
                }
            )
        groups[index_by_stem[stem]]["chunks"].append(chunk)
    return groups


def _write_chunks_partial(job_id: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "chunks": chunks,
        "grouped_by_lesson": _group_chunks(chunks),
        "updated_at": utc_now_iso(),
    }
    write_json(_workspace_file(job_id, "chunks_partial.json"), payload)
    return payload


def _chunk_id_set(values: list[Any] | None) -> set[str]:
    return {str(value) for value in values or [] if value is not None}


def _num_set(values: list[Any] | None) -> set[int]:
    nums = set()
    for value in values or []:
        parsed = _chunk_number(value, 0)
        if parsed and parsed != "0":
            nums.add(int(parsed))
    return nums


def ensure_chunk_preconditions(job_id: str) -> None:
    ensure_job_exists(job_id)
    config = read_json(job_config_path(job_id))
    source_pdf = Path(config.get("source_pdf_path") or "")
    if not source_pdf.exists():
        raise FileNotFoundError(f"Source PDF not found: {source_pdf}")
    if not _workspace_file(job_id, "approved_lessons.json").exists():
        raise FileNotFoundError("approved_lessons.json not found. Approve lessons before extracting chunks.")
    _state, _book_stem, bundle_dir = _find_bundle(job_id)
    lesson_dir = bundle_dir / "Lesson"
    if not lesson_dir.exists():
        raise FileNotFoundError(f"Lesson directory not found: {lesson_dir}")
    if not list(lesson_dir.rglob("*.pdf")):
        raise FileNotFoundError(f"No lesson PDFs found under: {lesson_dir}")


def _rebuild_canonical_topic_lesson(job_id: str, bundle_dir: Path, book_stem: str, source_pdf: str) -> None:
    topics_payload = read_json(_workspace_file(job_id, "approved_topics.json")) if _workspace_file(job_id, "approved_topics.json").exists() else {}
    lessons_payload = read_json(_workspace_file(job_id, "approved_lessons.json"))
    topics = _extract_items(topics_payload, "topics")
    lessons = _extract_items(lessons_payload, "lessons")
    _log(job_id, "canonical Topic/ rebuild started")
    _build_topic_pdfs(bundle_dir, book_stem, source_pdf, topics)
    _log(job_id, f"canonical Topic/ rebuild completed count={len(topics)}")
    _log(job_id, "canonical Lesson/ rebuild started")
    _build_lesson_pdfs(bundle_dir, book_stem, source_pdf, lessons)
    _log(job_id, f"canonical Lesson/ rebuild completed count={len(lessons)}")
    _write_bundle_manifest(bundle_dir, book_stem, topics, lessons)
    _log(job_id, "bundle manifest updated")


def extract_chunks_for_job(job_id: str) -> None:
    try:
        ensure_chunk_preconditions(job_id)
        config = read_json(job_config_path(job_id))
        source_pdf = str(Path(config["source_pdf_path"]))
        state, book_stem, bundle_dir = _find_bundle(job_id)
        approved_lessons = _extract_items(read_json(_workspace_file(job_id, "approved_lessons.json")), "lessons")

        update_job_state(job_id, status=JobStatus.extracting_chunks, stage="extracting_chunks")
        update_progress(
            job_id,
            status=JobStatus.extracting_chunks,
            stage="extracting_chunks",
            message="Chuẩn bị danh sách bài học đã duyệt...",
            percent=5,
            current=0,
            total=len(approved_lessons),
        )
        _log(job_id, "start extraction")
        _log(job_id, f"approved_lesson_count={len(approved_lessons)}")

        _rebuild_canonical_topic_lesson(job_id, bundle_dir, book_stem, source_pdf)
        lesson_pdf_count = len(list((bundle_dir / "Lesson").rglob("*.pdf")))
        _log(job_id, f"lesson_pdf_count={lesson_pdf_count}")
        update_progress(
            job_id,
            status=JobStatus.extracting_chunks,
            stage="preparing_lesson_pdfs",
            message="Đang chuẩn bị PDF bài học để cắt chunk...",
            percent=15,
            current=0,
            total=lesson_pdf_count,
        )

        key_manager = GeminiKeyManager.from_env()
        if key_manager.key_count() == 0:
            raise RuntimeError("No Gemini API keys configured. Set GEMINI_API_KEYS or GEMINI_API_KEY_1.")

        active = {"done": 0, "total": lesson_pdf_count}

        def status_cb(message: str) -> None:
            update_progress(
                job_id,
                status=JobStatus.extracting_chunks,
                stage="extracting_chunks",
                message=(message[:300] or "Đang gọi Gemini trích xuất chunk..."),
                percent=max(35, round(active["done"] * 55 / active["total"]) + 35) if active["total"] else 35,
                current=active["done"],
                total=active["total"],
            )
            _log(job_id, f"gemini: {message}")

        def progress_cb(done: int, total: int, lesson_pdf: Path) -> None:
            active["done"] = done
            active["total"] = total
            percent = round(done * 100 / total) if total else 0
            update_progress(
                job_id,
                status=JobStatus.extracting_chunks,
                stage="extracting_chunks",
                message=f"Đang cắt chunk {done}/{total}: {lesson_pdf.name}",
                percent=min(90, max(35, round(done * 55 / total) + 35)) if total else percent,
                current=done,
                total=total,
            )
            _log(job_id, f"chunk progress {done}/{total}: {lesson_pdf.name}")
            chunks = _collect_chunk_metas(bundle_dir, job_id)
            if chunks:
                _write_chunks_partial(job_id, chunks)

        summary = run_extract_and_split_chunks_for_book(
            key_manager,
            bundle_dir,
            model=get_settings().gemini_model,
            resume=False,
            progress_cb=progress_cb,
            status_cb=status_cb,
        )
        if summary.get("skipped_lessons"):
            raise RuntimeError(f"Chunk extraction failed for lessons: {summary['skipped_lessons']}")

        chunks = _collect_chunk_metas(bundle_dir, job_id)
        payload = _write_chunks_partial(job_id, chunks)
        _log(job_id, f"chunk_count={len(chunks)}")
        update_progress(
            job_id,
            status=JobStatus.extracting_chunks,
            stage="writing_chunks",
            message="Đang ghi dữ liệu chunk...",
            percent=90,
            current=len(chunks),
            total=len(chunks),
        )

        state["bundle_path"] = str(bundle_dir)
        state["book_stem"] = book_stem
        state["chunks_count"] = len(chunks)
        state["updated_at"] = utc_now_iso()
        write_json(_workspace_file(job_id, "extraction_state.json"), state)
        update_result(
            job_id,
            ok=True,
            status=JobStatus.reviewing_chunks,
            message="Đã trích xuất chunk, chờ duyệt.",
            data={"bundle_path": str(bundle_dir), "book_stem": book_stem, **payload},
        )
        update_progress(
            job_id,
            status=JobStatus.reviewing_chunks,
            stage="reviewing_chunks",
            message="Đã trích xuất chunk, chờ duyệt.",
            percent=100,
            current=lesson_pdf_count,
            total=lesson_pdf_count,
        )
        update_job_state(job_id, status=JobStatus.reviewing_chunks, stage="reviewing_chunks")
        _log(job_id, f"success chunks={len(chunks)} bundle_path={bundle_dir}")
    except Exception as exc:
        error = str(exc)
        try:
            if is_all_keys_cooldown_error(exc):
                mark_waiting_for_gemini_cooldown(
                    job_id,
                    retry_stage="extracting_chunks",
                    percent=35,
                    exc=exc,
                )
                _log(job_id, "waiting_gemini_cooldown")
                return
            update_job_state(job_id, status=JobStatus.error, stage="extracting_chunks", error=error)
            update_progress(job_id, status=JobStatus.error, stage="extracting_chunks", message=error, percent=0)
            update_result(job_id, ok=False, status=JobStatus.error, message="Chunk extraction failed.", error=error)
            _log(job_id, f"failure error={error}")
        except Exception:
            pass


def _approved_lesson_for_num(job_id: str, lesson_num: Any) -> dict[str, Any]:
    approved_path = _workspace_file(job_id, "approved_lessons.json")
    if not approved_path.exists():
        raise FileNotFoundError("approved_lessons.json not found. Approve the lesson before extracting chunks.")
    lessons = _extract_items(read_json(approved_path), "lessons")
    wanted = int(_chunk_number(lesson_num, 0))
    for lesson in lessons:
        if int(_chunk_number(lesson.get("lesson_num"), 0)) == wanted and lesson.get("approved") and lesson.get("metadata_edu_saved"):
            return lesson
    raise ValueError(f"Lesson {lesson_num} is not approved with Metadata-Edu saved.")


def _lesson_pdf_for_num(bundle_dir: Path, lesson_num: Any) -> Path:
    suffix = f"lesson_{int(_chunk_number(lesson_num, 0)):02d}"
    candidates = sorted((bundle_dir / "Lesson" / suffix).glob("*.pdf"))
    if not candidates:
        candidates = sorted((bundle_dir / "Lesson").glob(f"**/*{suffix}*.pdf"))
    if not candidates:
        raise FileNotFoundError(f"Lesson PDF not found for {suffix}.")
    return candidates[0]


def _merge_lesson_chunks(job_id: str, lesson_stem: str, new_chunks: list[dict[str, Any]]) -> dict[str, Any]:
    existing = []
    partial_path = _workspace_file(job_id, "chunks_partial.json")
    if partial_path.exists():
        existing = _extract_items(read_json(partial_path), "chunks")
    merged = [chunk for chunk in existing if chunk.get("lesson_stem") != lesson_stem] + new_chunks
    merged.sort(key=lambda item: (item.get("lesson_stem", ""), item.get("chunk", "")))
    return _write_chunks_partial(job_id, merged)


def extract_chunks_for_lesson(job_id: str, lesson_num: Any) -> None:
    try:
        ensure_chunk_preconditions(job_id)
        _approved_lesson_for_num(job_id, lesson_num)
        _state, book_stem, bundle_dir = _find_bundle(job_id)
        lesson_pdf = _lesson_pdf_for_num(bundle_dir, lesson_num)
        lesson_stem = lesson_pdf.stem
        update_job_state(job_id, status=JobStatus.extracting_chunks, stage="extracting_chunks_for_lesson")
        update_progress(
            job_id,
            status=JobStatus.extracting_chunks,
            stage="extracting_chunks_for_lesson",
            message=f"Đang trích xuất chunk cho Lesson {int(_chunk_number(lesson_num, 0)):02d}...",
            percent=5,
        )
        key_manager = GeminiKeyManager.from_env()
        if key_manager.key_count() == 0:
            raise RuntimeError("No Gemini API keys configured. Set GEMINI_API_KEYS or GEMINI_API_KEY_1.")

        total_pages = len(PdfReader(str(lesson_pdf)).pages)
        raw = extract_structure_from_pdf(
            key_manager,
            str(lesson_pdf),
            build_chunk_prompt_start_head(total_pages=total_pages),
            model=get_settings().gemini_model,
            status_cb=lambda message: _log(job_id, f"gemini lesson {lesson_num}: {message}"),
        )
        items = _flatten_start_head(raw.get("list_chunk")) if isinstance(raw.get("list_chunk"), list) else []
        filtered = []
        for start, content_head, heading, title in items:
            is_junk, _reason = _is_junk_candidate(heading, title)
            if is_junk:
                continue
            page_text = _extract_page_text(str(lesson_pdf), start)
            page_ok, _page_reason = _heading_valid_in_page(page_text, heading, title)
            if page_ok:
                filtered.append((start, content_head, heading, title))
        computed = _compute_chunks_from_start_head(filtered, total_pages)
        chunk_items = []
        for item in computed:
            chunk_name, obj = next(iter(item.items()))
            chunk_items.append({"chunk": chunk_name, **obj})

        rebuilt = rebuild_lesson_chunks(
            lesson_pdf=lesson_pdf,
            lesson_stem=lesson_stem,
            chunk_root=bundle_dir / "Chunk",
            chunk_items=chunk_items,
        )
        lookup = _lesson_lookup(job_id, bundle_dir)
        normalized = [_normalize_chunk(chunk, index, lookup) for index, chunk in enumerate(rebuilt)]
        payload = _merge_lesson_chunks(job_id, lesson_stem, normalized)
        write_json(job_workspace(job_id) / "chunks" / f"lesson_{int(_chunk_number(lesson_num, 0)):02d}" / "chunks_partial.json", {"chunks": normalized, "updated_at": utc_now_iso()})
        update_result(job_id, ok=True, status=JobStatus.reviewing_chunks, message=f"Đã trích xuất chunk cho Lesson {int(_chunk_number(lesson_num, 0)):02d}.", data=payload)
        update_progress(job_id, status=JobStatus.reviewing_chunks, stage="reviewing_chunks_for_lesson", message=f"Đã trích xuất chunk cho Lesson {int(_chunk_number(lesson_num, 0)):02d}, chờ duyệt.", percent=100, current=len(normalized), total=len(normalized))
        update_job_state(job_id, status=JobStatus.reviewing_chunks, stage="reviewing_chunks_for_lesson")
        _log(job_id, f"success per-lesson chunks lesson={lesson_num} count={len(normalized)}")
    except Exception as exc:
        error = str(exc)
        try:
            if is_all_keys_cooldown_error(exc):
                mark_waiting_for_gemini_cooldown(
                    job_id,
                    retry_stage="extracting_chunks_for_lesson",
                    percent=35,
                    exc=exc,
                )
                _log(job_id, "waiting_gemini_cooldown")
                return
            update_job_state(job_id, status=JobStatus.error, stage="extracting_chunks_for_lesson", error=error)
            update_progress(job_id, status=JobStatus.error, stage="extracting_chunks_for_lesson", message=error, percent=0)
            update_result(job_id, ok=False, status=JobStatus.error, message="Per-lesson chunk extraction failed.", error=error)
            _log(job_id, f"per-lesson failure error={error}")
        except Exception:
            pass


def read_chunks(job_id: str) -> dict[str, Any]:
    ensure_job_exists(job_id)
    approved_path = _workspace_file(job_id, "approved_chunks.json")
    partial_path = _workspace_file(job_id, "chunks_partial.json")
    if approved_path.exists():
        raw = read_json(approved_path)
        chunks = _extract_items(raw, "chunks")
        return {
            "ok": True,
            "job_id": job_id,
            "approved": bool(raw.get("approved_all", raw.get("approved", False))),
            "approved_all": bool(raw.get("approved_all", raw.get("approved", False))),
            "approved_chunk_ids": raw.get("approved_chunk_ids", []),
            "pending_chunk_ids": raw.get("pending_chunk_ids", []),
            "chunks": chunks,
            "grouped_by_lesson": raw.get("grouped_by_lesson") or _group_chunks(chunks),
            "raw": raw,
        }
    if partial_path.exists():
        raw = read_json(partial_path)
        chunks = _extract_items(raw, "chunks")
        return {"ok": True, "job_id": job_id, "approved": False, "chunks": chunks, "grouped_by_lesson": raw.get("grouped_by_lesson") or _group_chunks(chunks), "raw": raw}
    raise FileNotFoundError("No chunks found for this job.")


def _preview_roots(job_id: str) -> list[Path]:
    state_path = _workspace_file(job_id, "extraction_state.json")
    state = read_json(state_path) if state_path.exists() else {}
    roots: list[Path] = []
    for key in ["final_bundle_path", "bundle_path", "rebuilt_bundle_path"]:
        value = state.get(key)
        if value:
            roots.append(Path(value))
    book_stem = state.get("book_stem") or ""
    if book_stem:
        roots.append(job_workspace(job_id) / book_stem)
        roots.append(output_root() / book_stem)
    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(root)
    return unique


def _chunk_matches(meta: dict[str, Any], chunk_id: str) -> bool:
    candidates = {
        str(meta.get("chunk_id") or ""),
        str(meta.get("id") or ""),
        str(meta.get("chunk") or ""),
        str(meta.get("chunk_num") or ""),
        f"{meta.get('lesson_stem') or ''}:{meta.get('chunk') or meta.get('chunk_num') or ''}",
    }
    return chunk_id in candidates


def find_chunk_preview_pdf(job_id: str, chunk_id: str) -> Path:
    ensure_job_exists(job_id)
    chunks = read_chunks(job_id).get("chunks", [])
    target = next((chunk for chunk in chunks if str(chunk.get("chunk_id") or chunk.get("id")) == str(chunk_id)), None)
    if target:
        for key in ["pdf_path", "chunk_pdf", "final_pdf_path"]:
            value = target.get(key)
            if value and Path(value).exists():
                return Path(value)
    checked: list[str] = []
    for root in _preview_roots(job_id):
        chunk_root = root / "Chunk"
        checked.append(str(chunk_root))
        if not chunk_root.exists():
            continue
        for meta_path in sorted(chunk_root.glob("**/*.json")):
            if meta_path.name.endswith(".keywords.json"):
                continue
            try:
                meta = read_json(meta_path)
            except Exception:
                continue
            if _chunk_matches(meta, str(chunk_id)):
                for key in ["pdf_path", "chunk_pdf", "final_pdf_path"]:
                    value = meta.get(key)
                    if value and Path(value).exists():
                        return Path(value)
                candidates = sorted(meta_path.parent.glob("*.pdf"))
                if candidates:
                    return candidates[0]
        safe_tail = str(chunk_id).split(":", 1)[-1]
        candidates = sorted(chunk_root.glob(f"**/*{safe_tail}*.pdf"))
        if candidates:
            return candidates[0]
    raise FileNotFoundError(f"Chunk preview PDF not found for chunk_id={chunk_id}. Checked: {checked[:12]}")


def save_chunks(job_id: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_job_exists(job_id)
    normalized = [_normalize_chunk(chunk, index) for index, chunk in enumerate(chunks)]
    payload = _write_chunks_partial(job_id, normalized)
    update_result(job_id, ok=True, status=JobStatus.reviewing_chunks, message="Chunks updated.", data=payload)
    update_progress(job_id, status=JobStatus.reviewing_chunks, stage="reviewing_chunks", message="Chunks updated. Waiting for approval.", percent=100, current=len(normalized), total=len(normalized))
    update_job_state(job_id, status=JobStatus.reviewing_chunks, stage="reviewing_chunks")
    _log(job_id, f"chunks updated count={len(normalized)}")
    return {"ok": True, "job_id": job_id, "approved": False, **payload}


def _rewrite_lesson_chunks_from_flat(job_id: str, lesson_stem: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _state, _book_stem, bundle_dir = _find_bundle(job_id)
    lesson_pdfs = _lesson_pdf_map(bundle_dir)
    lesson_pdf = lesson_pdfs.get(lesson_stem)
    if not lesson_pdf:
        raise FileNotFoundError(f"Lesson PDF not found for lesson_stem={lesson_stem}")
    lesson_chunks = [chunk for chunk in chunks if chunk.get("lesson_stem") == lesson_stem]
    rebuilt = rebuild_lesson_chunks(
        lesson_pdf=lesson_pdf,
        lesson_stem=lesson_stem,
        chunk_root=bundle_dir / "Chunk",
        chunk_items=lesson_chunks,
    )
    merged = [chunk for chunk in chunks if chunk.get("lesson_stem") != lesson_stem] + rebuilt
    merged.sort(key=lambda item: (item.get("lesson_stem", ""), item.get("chunk", "")))
    _write_chunks_partial(job_id, merged)
    return merged


def add_chunk(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = read_chunks(job_id)["chunks"] if _workspace_file(job_id, "chunks_partial.json").exists() else []
    _state, _book_stem, bundle_dir = _find_bundle(job_id)
    lesson_stem = payload.get("lesson_stem")
    if not lesson_stem and payload.get("lesson_num") is not None:
        suffix = f"lesson_{int(_chunk_number(payload.get('lesson_num'), 1)):02d}"
        matches = [stem for stem in _lesson_pdf_map(bundle_dir) if stem.endswith(suffix)]
        lesson_stem = matches[0] if matches else None
    if not lesson_stem:
        raise ValueError("lesson_stem or lesson_num is required.")
    new_chunk = {
        **payload,
        "lesson_stem": lesson_stem,
        "chunk": payload.get("chunk_num") or f"chunk_{len([c for c in current if c.get('lesson_stem') == lesson_stem]) + 1:02d}",
        "title": payload.get("title") or payload.get("chunk_name") or "",
        "content_head": bool(payload.get("content_head", False)),
    }
    updated = current + [_normalize_chunk(new_chunk, len(current))]
    rebuilt = _rewrite_lesson_chunks_from_flat(job_id, lesson_stem, updated)
    return {"ok": True, "job_id": job_id, "chunks": rebuilt, "grouped_by_lesson": _group_chunks(rebuilt), "count": len(rebuilt)}


def delete_chunk(job_id: str, chunk_id: str) -> dict[str, Any]:
    chunks = read_chunks(job_id)["chunks"]
    target = next((chunk for chunk in chunks if chunk.get("chunk_id") == chunk_id or chunk.get("id") == chunk_id), None)
    if target is None:
        raise FileNotFoundError(f"Chunk not found: {chunk_id}")
    lesson_stem = target.get("lesson_stem")
    remaining = [chunk for chunk in chunks if chunk is not target and chunk.get("chunk_id") != chunk_id and chunk.get("id") != chunk_id]
    rebuilt = _rewrite_lesson_chunks_from_flat(job_id, lesson_stem, remaining)
    return {"ok": True, "job_id": job_id, "chunks": rebuilt, "grouped_by_lesson": _group_chunks(rebuilt), "count": len(rebuilt)}


def recut_chunk(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    chunks = read_chunks(job_id)["chunks"]
    lesson_stem = payload["lesson_stem"]
    target_id = payload.get("chunk_id")
    chunk_num = payload.get("chunk_num")
    updated = []
    found = False
    for chunk in chunks:
        same = chunk.get("lesson_stem") == lesson_stem and (
            (target_id and (chunk.get("chunk_id") == target_id or chunk.get("id") == target_id))
            or (chunk_num is not None and str(chunk.get("chunk_num")) == str(chunk_num))
        )
        if same:
            found = True
            updated.append({**chunk, **payload})
        else:
            updated.append(chunk)
    if not found:
        raise FileNotFoundError("Chunk to recut not found.")
    rebuilt = _rewrite_lesson_chunks_from_flat(job_id, lesson_stem, updated)
    return {"ok": True, "job_id": job_id, "chunks": rebuilt, "grouped_by_lesson": _group_chunks(rebuilt), "count": len(rebuilt)}


def _load_chunk_approval_payload(job_id: str) -> dict[str, Any]:
    partial_chunks = []
    if _workspace_file(job_id, "chunks_partial.json").exists():
        partial_chunks = _extract_items(read_json(_workspace_file(job_id, "chunks_partial.json")), "chunks")
    by_id = {}
    for index, chunk in enumerate(partial_chunks):
        item = _normalize_chunk(chunk, index)
        by_id[str(item.get("chunk_id") or item.get("id"))] = item
    approved_ids: set[str] = set()
    if _workspace_file(job_id, "approved_chunks.json").exists():
        raw = read_json(_workspace_file(job_id, "approved_chunks.json"))
        for index, chunk in enumerate(_extract_items(raw, "chunks")):
            item = _normalize_chunk(chunk, index)
            cid = str(item.get("chunk_id") or item.get("id"))
            by_id[cid] = {**by_id.get(cid, {}), **item}
            if item.get("approved") or raw.get("approved") is True:
                approved_ids.add(cid)
        approved_ids.update(_chunk_id_set(raw.get("approved_chunk_ids")))
    chunks = []
    for cid in sorted(by_id):
        chunk = by_id[cid]
        chunk["approved"] = cid in approved_ids
        chunks.append(chunk)
    pending = [str(chunk.get("chunk_id") or chunk.get("id")) for chunk in chunks if not chunk.get("approved")]
    approved_all = bool(chunks) and not pending
    return {
        "approved": approved_all,
        "approved_all": approved_all,
        "approved_chunk_ids": sorted(approved_ids),
        "pending_chunk_ids": pending,
        "chunks": chunks,
        "grouped_by_lesson": _group_chunks(chunks),
        "updated_at": utc_now_iso(),
    }


def approve_chunks(
    job_id: str,
    chunks: list[dict[str, Any]] | None = None,
    chunk_ids: list[Any] | None = None,
) -> dict[str, Any]:
    ensure_job_exists(job_id)
    if chunks is not None:
        save_chunks(job_id, chunks)
    current = _load_chunk_approval_payload(job_id)
    all_chunks = current["chunks"]
    if not all_chunks:
        raise ValueError("Chunk list is empty.")
    selected_ids = _chunk_id_set(chunk_ids)
    if not selected_ids:
        selected_ids = {str(chunk.get("chunk_id") or chunk.get("id")) for chunk in all_chunks}
    approved_ids = set(current["approved_chunk_ids"])
    approved_at = utc_now_iso()
    changed = 0
    for chunk in all_chunks:
        cid = str(chunk.get("chunk_id") or chunk.get("id"))
        if cid not in selected_ids:
            continue
        chunk.update(
            {
                "approved": True,
                "approved_at": approved_at,
                "metadata_edu_saved": False,
                "minio_uploaded": False,
                "waiting_for_kaggle": True,
            }
        )
        approved_ids.add(cid)
        changed += 1
    if changed == 0:
        raise ValueError("Selected chunk_ids were not found.")
    pending = [str(chunk.get("chunk_id") or chunk.get("id")) for chunk in all_chunks if str(chunk.get("chunk_id") or chunk.get("id")) not in approved_ids]
    approved_all = bool(all_chunks) and not pending
    payload = {
        "chunks": all_chunks,
        "grouped_by_lesson": _group_chunks(all_chunks),
        "approved": approved_all,
        "approved_all": approved_all,
        "approved_chunk_ids": sorted(approved_ids),
        "pending_chunk_ids": pending,
        "approved_at": approved_at,
        "waiting_for_kaggle": True,
    }
    write_json(_workspace_file(job_id, "approved_chunks.json"), payload)
    update_job_state(job_id, status=JobStatus.reviewing_chunks, stage="reviewing_chunks")
    message = "Đã duyệt chunk. Đang lưu metadata và sync PostgreSQL + Neo4j."
    update_result(job_id, ok=True, status=JobStatus.reviewing_chunks, message=message, data=payload)
    update_progress(job_id, status=JobStatus.reviewing_chunks, stage="reviewing_chunks", message=message, percent=100, current=len(approved_ids), total=len(all_chunks))
    _log(job_id, f"chunks approved selected={sorted(selected_ids)} approved_total={len(approved_ids)}")

    from app.services.chunk_metadata_service import save_chunks_metadata_and_sync
    sync_result = save_chunks_metadata_and_sync(job_id, chunk_ids=sorted(selected_ids))
    approved = read_json(_workspace_file(job_id, "approved_chunks.json"))
    payload = {
        **approved,
        "grouped_by_lesson": _group_chunks(approved.get("chunks", [])),
        "approved": approved.get("approved", approved_all),
        "approved_all": approved.get("approved_all", approved_all),
    }
    _log(job_id, f"chunks metadata saved and synced result={sync_result}")
    return {"ok": True, "job_id": job_id, "approved": approved_all, **payload}


def approve_chunk(job_id: str, chunk_id: Any) -> dict[str, Any]:
    return approve_chunks(job_id, chunk_ids=[chunk_id])
