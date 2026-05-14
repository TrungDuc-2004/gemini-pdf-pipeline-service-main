from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.core.config import get_settings
from app.core.gemini_keys import GeminiKeyManager
from app.core.logging import append_job_log
from app.core.paths import (
    job_config_path,
    job_log_path,
    job_progress_path,
    job_result_path,
    job_source_pdf_path,
    job_state_path,
    job_workspace,
    output_root,
)
from app.models.job_models import JobStatus
from app.pipeline.les_top_pipeline import run_extract_save_split
from app.pipeline.pdf_output import flatten_manifest_items
from app.services.job_service import ensure_job_exists, ensure_job_state, update_job_state
from app.services.gemini_cooldown_service import is_all_keys_cooldown_error, mark_waiting_for_gemini_cooldown
from app.services.progress_service import update_progress, update_result
from app.services.topic_metadata_service import save_topic_metadata_for_job
from app.utils.files import read_json, write_json
from app.utils.time import utc_now_iso


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-")
    return slug or "book"


def _topic_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "topics.log"


def _log(job_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}"
    append_job_log(_topic_log_path(job_id), line)
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [topics] {message}")


def _workspace_file(job_id: str, name: str) -> Path:
    return job_workspace(job_id) / name


def _pad2(value: Any) -> str:
    try:
        return f"{int(value):02d}"
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value or ""))
        return f"{int(match.group(0)):02d}" if match else "00"


def _log_state_files(job_id: str, label: str) -> None:
    paths = {
        "job_state": job_state_path(job_id),
        "job_config": job_config_path(job_id),
        "progress": job_progress_path(job_id),
        "result": job_result_path(job_id),
        "source_pdf": job_source_pdf_path(job_id),
    }
    summary = " ".join(f"{name}_exists={path.exists()}" for name, path in paths.items())
    _log(job_id, f"{label} {summary}")


def _normalize_topic_for_api(item: dict[str, Any], index: int) -> dict[str, Any]:
    heading = item.get("heading") or item.get("raw_heading") or ""
    title = item.get("title") or item.get("raw_title") or item.get("topic_name") or ""
    return {
        **item,
        "topic_num": item.get("topic_num") or re.sub(r"\D+", "", str(heading)) or str(index + 1),
        "topic_name": item.get("topic_name") or title,
        "raw_heading": item.get("raw_heading") or heading,
        "raw_title": item.get("raw_title") or title,
    }


def _topic_num_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value or ""))
        return int(match.group(0)) if match else None


def _topic_num_set(values: list[Any] | None) -> set[int]:
    nums: set[int] = set()
    for value in values or []:
        parsed = _topic_num_int(value)
        if parsed is not None:
            nums.add(parsed)
    return nums


def _topics_from_partial(job_id: str) -> list[dict[str, Any]]:
    partial_path = _workspace_file(job_id, "topics_partial.json")
    if not partial_path.exists():
        return []
    raw = read_json(partial_path)
    topics = raw.get("topics", raw) if isinstance(raw, dict) else raw
    if not isinstance(topics, list):
        return []
    return [_normalize_topic_for_api(dict(topic), index) for index, topic in enumerate(topics) if isinstance(topic, dict)]


def _topic_preview_search(job_id: str, topic_num: Any) -> dict[str, Any]:
    state_path = _workspace_file(job_id, "extraction_state.json")
    state = read_json(state_path) if state_path.exists() else {}
    book_stem = state.get("book_stem") or ""
    roots: list[Path] = []
    for key in ["bundle_path", "rebuilt_bundle_path", "final_bundle_path"]:
        value = state.get(key)
        if value:
            roots.append(Path(value))
    roots.append(job_workspace(job_id))
    if book_stem:
        roots.append(job_workspace(job_id) / book_stem)
        roots.append(output_root() / book_stem)
    roots.append(output_root())

    topic_id = f"topic_{_pad2(topic_num)}"
    checked_paths: list[str] = [str(state_path)]
    direct_candidates: list[Path] = []
    named_candidates: list[Path] = []
    meta_candidates: list[Path] = []
    for root in roots:
        checked_paths.append(str(root))
        topic_roots = [root / "Topic"]
        if root.name == "Topic":
            topic_roots.append(root)
        for topic_root in topic_roots:
            direct = topic_root / topic_id
            checked_paths.extend([str(direct), str(topic_root / f"**/*{topic_id}*.pdf")])
            if direct.exists():
                direct_candidates.extend(sorted(direct.glob("*.pdf")))
            if not topic_root.exists():
                continue
            named_candidates.extend(sorted(topic_root.glob(f"**/*{topic_id}*.pdf")))
            for meta_path in sorted(topic_root.rglob("*.json")):
                try:
                    meta = read_json(meta_path)
                except Exception:
                    continue
                if _topic_num_int(meta.get("topic_num")) == _topic_num_int(topic_num):
                    meta_candidates.extend(sorted(meta_path.parent.glob("*.pdf")))

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in [*direct_candidates, *named_candidates, *meta_candidates]:
        resolved = path.resolve()
        if path.exists() and resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return {
        "book_stem": book_stem,
        "checked_paths": checked_paths[:80],
        "pdf_candidates": [str(path) for path in unique[:80]],
        "candidates": unique,
        "extraction_state_exists": state_path.exists(),
    }


def _topic_pdf_candidates(job_id: str, topic_num: Any, checked_paths: list[str] | None = None) -> list[Path]:
    result = _topic_preview_search(job_id, topic_num)
    if checked_paths is not None:
        checked_paths.extend(result["checked_paths"])
    return result["candidates"]


def find_topic_preview_pdf(job_id: str, topic_num: Any) -> Path:
    ensure_job_exists(job_id)
    result = _topic_preview_search(job_id, topic_num)
    candidates = result["candidates"]
    if not candidates:
        raise FileNotFoundError(f"Không tìm thấy file PDF preview cho Topic {_pad2(topic_num)}.")
    return candidates[0]


def topic_preview_info(job_id: str, topic_num: Any) -> dict[str, Any]:
    ensure_job_exists(job_id)

    # Try to read topics — if extraction hasn't run yet, fall back to a synthetic placeholder.
    topic: dict[str, Any] = {}
    topics_available = False
    try:
        topics_data = read_topics(job_id)
        matched = next(
            (item for item in topics_data.get("topics", []) if _topic_num_int(item.get("topic_num")) == _topic_num_int(topic_num)),
            None,
        )
        if matched is not None:
            topic = matched
            topics_available = True
    except FileNotFoundError:
        # topics_partial.json not yet written — extraction not run or failed
        pass

    search = _topic_preview_search(job_id, topic_num)
    candidates = search["candidates"]
    local_available = bool(candidates)
    asset_object_key = topic.get("asset_object_key")

    payload: dict[str, Any] = {
        "ok": True,
        "job_id": job_id,
        "topic_num": topic.get("topic_num") or topic_num,
        "topic_name": topic.get("topic_name") or f"Topic {_pad2(topic_num)}",
        "topics_extracted": topics_available,
        "local_preview_available": local_available,
        "local_preview_url": f"/api/jobs/{job_id}/topics/{topic_num}/preview" if local_available else None,
        "local_preview_path": str(candidates[0]) if local_available else None,
        "minio_available": bool(topic.get("asset_url") or asset_object_key),
        "asset_url": topic.get("asset_url"),
        "direct_minio_url": topic.get("asset_url"),
        "backend_preview_url": f"/api/jobs/{job_id}/topics/{topic_num}/preview",
        "source_preview_url": f"/api/jobs/{job_id}/source/preview",
        "asset_object_key": asset_object_key,
        "object_key": asset_object_key,
        "approved": bool(topic.get("approved")),
        "metadata_edu_saved": bool(topic.get("metadata_edu_saved")),
        "minio_uploaded": bool(topic.get("minio_uploaded")),
        "debug": {
            "book_stem": search["book_stem"],
            "extraction_state_exists": search["extraction_state_exists"],
            "checked_paths": search["checked_paths"][:30],
            "pdf_candidates": search["pdf_candidates"][:30],
        },
    }
    if not local_available:
        payload["checked_paths"] = search["checked_paths"][:20]
    return payload


def _normalize_approved_topics_payload(job_id: str) -> dict[str, Any]:
    approved_path = _workspace_file(job_id, "approved_topics.json")
    partial_topics = _topics_from_partial(job_id)
    topic_by_num = {_topic_num_int(topic.get("topic_num")): topic for topic in partial_topics}

    approved_nums: set[int] = set()
    approved_details: dict[int, dict[str, Any]] = {}
    if approved_path.exists():
        raw = read_json(approved_path)
        if isinstance(raw, list):
            for index, topic in enumerate(raw):
                if isinstance(topic, dict):
                    item = _normalize_topic_for_api(topic, index)
                    num = _topic_num_int(item.get("topic_num"))
                    if num is not None:
                        approved_nums.add(num)
                        approved_details[num] = {**item, "approved": True}
        elif isinstance(raw, dict):
            raw_topics = raw.get("topics", [])
            if isinstance(raw_topics, list):
                for index, topic in enumerate(raw_topics):
                    if isinstance(topic, dict):
                        item = _normalize_topic_for_api(topic, index)
                        num = _topic_num_int(item.get("topic_num"))
                        if num is not None:
                            topic_by_num[num] = {**topic_by_num.get(num, {}), **item}
                            if item.get("approved") or raw.get("approved") is True:
                                approved_nums.add(num)
                                approved_details[num] = {**item, "approved": True}
            approved_nums.update(_topic_num_set(raw.get("approved_topic_nums")))

    all_nums = [num for num in sorted(topic_by_num) if num is not None]
    topics_out: list[dict[str, Any]] = []
    for num in all_nums:
        topic = {**topic_by_num[num], **approved_details.get(num, {})}
        topic["approved"] = num in approved_nums
        topics_out.append(topic)

    approved_all = bool(all_nums) and all(num in approved_nums for num in all_nums)
    pending_nums = [num for num in all_nums if num not in approved_nums]
    return {
        "approved_all": approved_all,
        "approved": approved_all,
        "approved_topic_nums": sorted(approved_nums),
        "pending_topic_nums": pending_nums,
        "topics": topics_out,
        "updated_at": utc_now_iso(),
    }


def _write_metadata_edu_topic_state(
    job_id: str,
    *,
    approved_nums: list[int],
    saved_nums: list[int],
    pending_nums: list[int],
    topic_assets: dict[str, Any],
) -> None:
    state_path = job_state_path(job_id)
    state = read_json(state_path) if state_path.exists() else {}
    metadata_edu = dict(state.get("metadata_edu") or {})
    topics_state = dict(metadata_edu.get("topics") or {})
    topics_state.update(
        {
            "approved_topic_nums": approved_nums,
            "saved_topic_nums": saved_nums,
            "pending_topic_nums": pending_nums,
            "topic_assets": topic_assets,
            "updated_at": utc_now_iso(),
        }
    )
    metadata_edu["topics"] = topics_state
    write_json(state_path, {**state, "metadata_edu": metadata_edu, "updated_at": utc_now_iso()})


def extract_topics_for_job(job_id: str) -> None:
    try:
        ensure_job_exists(job_id)
        ensure_job_state(job_id)
        _log_state_files(job_id, "extraction_start")
        config = read_json(job_config_path(job_id))
        source_pdf = Path(config["source_pdf_path"])
        if not source_pdf.exists():
            raise FileNotFoundError(f"Source PDF not found: {source_pdf}")

        settings = get_settings()
        book_stem = f"{_slugify(config.get('book_name', source_pdf.stem))}_{job_id[:8]}"
        bundle_dir = job_workspace(job_id) / book_stem

        update_job_state(job_id, status=JobStatus.extracting_topics, stage="extracting_topics")
        _log_state_files(job_id, "after_status_extracting_topics")
        update_progress(
            job_id,
            status=JobStatus.extracting_topics,
            stage="preparing_topics",
            message="Chuẩn bị file PDF...",
            percent=5,
        )
        _log(job_id, "start extraction")
        _log(job_id, f"source_pdf={source_pdf}")
        _log(job_id, f"model={settings.gemini_model}")

        total_pages = len(PdfReader(str(source_pdf)).pages)
        _log(job_id, f"pdf_pages={total_pages}")
        update_progress(
            job_id,
            status=JobStatus.extracting_topics,
            stage="uploading_pdf_to_gemini",
            message="Đang upload PDF lên Gemini...",
            percent=15,
            current=0,
            total=total_pages,
        )

        key_manager = GeminiKeyManager.from_env()
        if key_manager.key_count() == 0:
            raise RuntimeError("No Gemini API keys configured. Set GEMINI_API_KEYS or GEMINI_API_KEY_1.")

        def progress_cb(stage: str, message: str, current: int = 0, total: int = 0) -> None:
            percent = round(current * 100 / total) if total else 0
            update_progress(
                job_id,
                status=JobStatus.extracting_topics,
                stage=stage,
                message=message[:300],
                percent=percent,
                current=current,
                total=total,
            )
            _log(job_id, f"{stage}: {message}")

        def status_cb(message: str) -> None:
            progress_cb("waiting_gemini_topics", message)

        progress_cb("calling_gemini_topics", "Đang gọi Gemini trích xuất chủ đề.", 35, 100)
        manifest, manifest_path, split_result = run_extract_save_split(
            key_manager,
            str(source_pdf),
            model=settings.gemini_model,
            output_root=bundle_dir,
            book_stem=book_stem,
            progress_cb=progress_cb,
            status_cb=status_cb,
        )

        topics = [
            _normalize_topic_for_api(item, index)
            for index, item in enumerate(flatten_manifest_items(manifest.get("list_topic", [])))
        ]
        raw_lessons = flatten_manifest_items(manifest.get("list_lesson", []))

        update_progress(
            job_id,
            status=JobStatus.extracting_topics,
            stage="writing_topics",
            message="Đang ghi dữ liệu chủ đề...",
            percent=90,
            current=len(topics),
            total=len(topics),
        )
        ensure_job_state(job_id)
        _log_state_files(job_id, "before_topic_state_writes")

        write_json(_workspace_file(job_id, "topics_partial.json"), {"topics": topics})
        write_json(
            _workspace_file(job_id, "extraction_state.json"),
            {
                "bundle_path": str(bundle_dir),
                "book_stem": book_stem,
                "manifest_path": manifest_path,
                "raw_lessons": raw_lessons,
                "topic_pdf_paths": split_result.get("topics", []),
                "lesson_pdf_paths": split_result.get("lessons", []),
                "updated_at": utc_now_iso(),
            },
        )

        update_result(
            job_id,
            ok=True,
            status=JobStatus.reviewing_topics,
            message="Đã trích xuất chủ đề, chờ duyệt.",
            data={
                "bundle_path": str(bundle_dir),
                "book_stem": book_stem,
                "topics": topics,
            },
        )
        update_progress(
            job_id,
            status=JobStatus.reviewing_topics,
            stage="reviewing_topics",
            message="Đã trích xuất chủ đề, chờ duyệt.",
            percent=100,
            current=len(topics),
            total=len(topics),
        )
        update_job_state(job_id, status=JobStatus.reviewing_topics, stage="reviewing_topics")
        _log_state_files(job_id, "after_success_reviewing_topics")
        _log(job_id, f"success topics={len(topics)} bundle_path={bundle_dir}")
    except Exception as exc:
        error = str(exc)
        try:
            ensure_job_state(job_id)
            if is_all_keys_cooldown_error(exc):
                mark_waiting_for_gemini_cooldown(
                    job_id,
                    retry_stage="extracting_topics",
                    percent=35,
                    exc=exc,
                )
                _log_state_files(job_id, "after_waiting_gemini_cooldown")
                _log(job_id, "waiting_gemini_cooldown")
                return
            update_job_state(job_id, status=JobStatus.error, stage="extracting_topics", error=error)
            update_progress(
                job_id,
                status=JobStatus.error,
                stage="extracting_topics",
                message=error,
                percent=0,
            )
            update_result(
                job_id,
                ok=False,
                status=JobStatus.error,
                message="Topic extraction failed.",
                error=error,
            )
            _log_state_files(job_id, "after_failure_error_state")
            _log(job_id, f"failure error={error}")
        except Exception:
            pass


def read_topics(job_id: str) -> dict[str, Any]:
    ensure_job_exists(job_id)
    partial_path = _workspace_file(job_id, "topics_partial.json")
    if partial_path.exists():
        data = _normalize_approved_topics_payload(job_id)
        return {"ok": True, "job_id": job_id, **data}
    approved_path = _workspace_file(job_id, "approved_topics.json")
    if approved_path.exists():
        data = _normalize_approved_topics_payload(job_id)
        return {"ok": True, "job_id": job_id, **data}
    raise FileNotFoundError("No topics found for this job.")


def save_topics(job_id: str, topics: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_job_exists(job_id)
    payload = {"topics": topics, "updated_at": utc_now_iso()}
    write_json(_workspace_file(job_id, "topics_partial.json"), payload)
    update_result(
        job_id,
        ok=True,
        status=JobStatus.reviewing_topics,
        message="Topics updated.",
        data={"topics": topics},
    )
    update_progress(
        job_id,
        status=JobStatus.reviewing_topics,
        stage="reviewing_topics",
        message="Topics updated. Waiting for approval.",
        percent=100,
        current=len(topics),
        total=len(topics),
    )
    _log(job_id, f"topics updated count={len(topics)}")
    return {"ok": True, "job_id": job_id, "approved": False, "topics": topics}


def approve_topics(
    job_id: str,
    topics: list[dict[str, Any]] | None = None,
    topic_nums: list[Any] | None = None,
) -> dict[str, Any]:
    ensure_job_exists(job_id)
    if topics is not None:
        save_topics(job_id, topics)

    current = _normalize_approved_topics_payload(job_id)
    all_topics = current["topics"]
    if not all_topics:
        raise ValueError("Topic list is empty.")

    selected_nums = _topic_num_set(topic_nums)
    if not selected_nums:
        selected_nums = {_topic_num_int(topic.get("topic_num")) for topic in all_topics}
        selected_nums = {num for num in selected_nums if num is not None}

    approved_nums = set(current["approved_topic_nums"])
    topic_assets = {}
    state_path = job_state_path(job_id)
    if state_path.exists():
        state = read_json(state_path)
        topic_assets = dict(((state.get("metadata_edu") or {}).get("topics") or {}).get("topic_assets") or {})

    approved_at = utc_now_iso()
    changed_count = 0
    for topic in all_topics:
        num = _topic_num_int(topic.get("topic_num"))
        if num not in selected_nums:
            continue
        summary = save_topic_metadata_for_job(job_id, topic)
        topic["approved"] = True
        topic["approved_at"] = approved_at
        topic["metadata_edu_saved"] = True
        topic["minio_uploaded"] = True
        topic["asset_object_key"] = summary["object_key"]
        topic["asset_url"] = summary["url"]
        topic["topic_id"] = summary["topic_id"]
        topic["asset_id"] = summary["asset_id"]
        topic_assets[str(num)] = summary
        approved_nums.add(num)
        changed_count += 1

    if changed_count == 0:
        raise ValueError("Selected topic_nums were not found.")

    all_nums = [num for num in (_topic_num_int(topic.get("topic_num")) for topic in all_topics) if num is not None]
    pending_nums = [num for num in sorted(all_nums) if num not in approved_nums]
    approved_all = bool(all_nums) and not pending_nums
    payload = {
        "approved_all": approved_all,
        "approved_topic_nums": sorted(approved_nums),
        "pending_topic_nums": pending_nums,
        "topics": all_topics,
        "updated_at": approved_at,
    }
    write_json(_workspace_file(job_id, "approved_topics.json"), payload)
    update_job_state(job_id, status=JobStatus.reviewing_topics, stage="reviewing_topics")
    _write_metadata_edu_topic_state(
        job_id,
        approved_nums=sorted(approved_nums),
        saved_nums=sorted(approved_nums),
        pending_nums=pending_nums,
        topic_assets=topic_assets,
    )
    if approved_all:
        message = "Đã duyệt toàn bộ chủ đề. Có thể trích xuất bài học."
    elif len(selected_nums) == 1:
        message = f"Đã duyệt Topic {next(iter(selected_nums)):02d} và lưu PDF chủ đề lên MinIO."
    else:
        message = f"Đã duyệt {changed_count} chủ đề và lưu PDF chủ đề lên MinIO."
    update_result(
        job_id,
        ok=True,
        status=JobStatus.reviewing_topics,
        message=message,
        data=payload,
    )
    update_progress(
        job_id,
        status=JobStatus.reviewing_topics,
        stage="reviewing_topics",
        message=message,
        percent=100,
        current=len(approved_nums),
        total=len(all_nums),
    )
    _log(job_id, f"topics approved selected={sorted(selected_nums)} approved_total={len(approved_nums)} pending={pending_nums}")
    return {"ok": True, "job_id": job_id, "approved": approved_all, **payload}


def approve_topic(job_id: str, topic_num: Any) -> dict[str, Any]:
    return approve_topics(job_id, topic_nums=[topic_num])
