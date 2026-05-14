from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from app.core.logging import append_job_log
from app.core.paths import job_config_path, job_log_path, job_workspace, output_root
from app.models.job_models import JobStatus
from app.services.gemini_cooldown_service import is_all_keys_cooldown_error, mark_waiting_for_gemini_cooldown
from app.services.job_service import ensure_job_exists, update_job_state
from app.services.lesson_metadata_service import save_lesson_metadata_for_job
from app.services.progress_service import update_progress, update_result
from app.utils.files import read_json, write_json
from app.utils.time import utc_now_iso


def _workspace_file(job_id: str, name: str) -> Path:
    return job_workspace(job_id) / name


def _lesson_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "lessons.log"


def _log(job_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}"
    append_job_log(_lesson_log_path(job_id), line)
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [lessons] {message}")


def _num_from_heading(heading: str) -> str:
    match = re.search(r"\d+", str(heading or ""))
    return match.group(0) if match else ""


def _safe_folder_name(value: str) -> str:
    return str(value).replace("/", "_").replace("\\", "_").strip()


def _extract_items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get(key, payload.get("items", []))
    else:
        items = payload
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _normalize_topic(topic: dict[str, Any], index: int) -> dict[str, Any]:
    heading = topic.get("heading") or topic.get("raw_heading") or (
        f"Chủ đề {topic.get('topic_num')}." if topic.get("topic_num") else ""
    )
    title = topic.get("title") or topic.get("raw_title") or topic.get("topic_name") or ""
    name = topic.get("name") or f"topic_{index + 1:02d}"
    topic_num = str(topic.get("topic_num") or _num_from_heading(heading) or index + 1)
    return {
        **topic,
        "name": _safe_folder_name(name),
        "start": int(topic.get("start") or 1),
        "end": int(topic.get("end") or topic.get("start") or 1),
        "heading": str(heading or ""),
        "title": str(title or ""),
        "topic_num": topic_num,
        "topic_name": str(topic.get("topic_name") or title or ""),
        "raw_heading": str(topic.get("raw_heading") or heading or ""),
        "raw_title": str(topic.get("raw_title") or title or ""),
    }


def _normalize_lesson(
    lesson: dict[str, Any],
    index: int,
    topic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    heading = lesson.get("heading") or lesson.get("raw_heading") or (
        f"Bài {lesson.get('lesson_num')}." if lesson.get("lesson_num") else ""
    )
    title = lesson.get("title") or lesson.get("raw_title") or lesson.get("lesson_name") or ""
    name = lesson.get("name") or f"lesson_{index + 1:02d}"
    lesson_num = str(lesson.get("lesson_num") or _num_from_heading(heading) or index + 1)
    topic_num = lesson.get("topic_num") or (topic or {}).get("topic_num")
    topic_name = lesson.get("topic_name") or (topic or {}).get("topic_name")
    return {
        **lesson,
        "name": _safe_folder_name(name),
        "start": int(lesson.get("start") or 1),
        "end": int(lesson.get("end") or lesson.get("start") or 1),
        "heading": str(heading or ""),
        "title": str(title or ""),
        "lesson_num": lesson_num,
        "lesson_name": str(lesson.get("lesson_name") or title or ""),
        "topic_num": str(topic_num or ""),
        "topic_name": str(topic_name or ""),
        "raw_heading": str(lesson.get("raw_heading") or heading or ""),
        "raw_title": str(lesson.get("raw_title") or title or ""),
    }


def _topic_num_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value or ""))
        return int(match.group(0)) if match else None


def _pad2(value: Any) -> str:
    parsed = _topic_num_int(value)
    return f"{parsed:02d}" if parsed is not None else "00"


def _slice_pdf(source_pdf: str, start: int, end: int, out_path: Path) -> None:
    reader = PdfReader(source_pdf)
    total = len(reader.pages)
    safe_start = max(1, min(int(start), total))
    safe_end = max(safe_start, min(int(end), total))
    writer = PdfWriter()
    for page_index in range(safe_start - 1, safe_end):
        writer.add_page(reader.pages[page_index])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as file:
        writer.write(file)


def _build_topic_pdfs(bundle_dir: Path, book_stem: str, source_pdf: str, topics: list[dict[str, Any]]) -> None:
    topic_dir = bundle_dir / "Topic"
    if topic_dir.exists():
        shutil.rmtree(topic_dir)
    topic_dir.mkdir(parents=True, exist_ok=True)

    for index, topic in enumerate(topics):
        item = _normalize_topic(topic, index)
        safe_name = item["name"] if item["name"].startswith("topic_") else f"topic_{index + 1:02d}"
        folder = topic_dir / safe_name
        out_pdf = folder / f"{book_stem}_{safe_name}.pdf"
        _slice_pdf(source_pdf, item["start"], item["end"], out_pdf)
        meta = {
            "kind": "topic",
            "name": safe_name,
            "start": item["start"],
            "end": item["end"],
            "source_pdf": str(Path(source_pdf).resolve()),
            "pdf": str(out_pdf.resolve()),
            "topic_num": item["topic_num"],
            "topic_name": item["topic_name"],
            "heading": item["heading"],
            "title": item["title"],
            "raw_heading": item["raw_heading"],
            "raw_title": item["raw_title"],
        }
        out_pdf.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _build_lesson_pdfs(bundle_dir: Path, book_stem: str, source_pdf: str, lessons: list[dict[str, Any]]) -> None:
    lesson_dir = bundle_dir / "Lesson"
    if lesson_dir.exists():
        shutil.rmtree(lesson_dir)
    lesson_dir.mkdir(parents=True, exist_ok=True)

    for index, lesson in enumerate(lessons):
        item = _normalize_lesson(lesson, index)
        safe_name = item["name"] if item["name"].startswith("lesson_") else f"lesson_{index + 1:02d}"
        folder = lesson_dir / safe_name
        out_pdf = folder / f"{book_stem}_{safe_name}.pdf"
        _slice_pdf(source_pdf, item["start"], item["end"], out_pdf)
        meta = {
            "kind": "lesson",
            "name": safe_name,
            "start": item["start"],
            "end": item["end"],
            "source_pdf": str(Path(source_pdf).resolve()),
            "pdf": str(out_pdf.resolve()),
            "lesson_num": item["lesson_num"],
            "lesson_name": item["lesson_name"],
            "topic_num": item["topic_num"],
            "topic_name": item["topic_name"],
            "heading": item["heading"],
            "title": item["title"],
            "raw_heading": item["raw_heading"],
            "raw_title": item["raw_title"],
        }
        out_pdf.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _build_lesson_pdfs_for_topic(bundle_dir: Path, book_stem: str, source_pdf: str, lessons: list[dict[str, Any]]) -> None:
    lesson_dir = bundle_dir / "Lesson"
    lesson_dir.mkdir(parents=True, exist_ok=True)

    for index, lesson in enumerate(lessons):
        item = _normalize_lesson(lesson, index)
        safe_name = item["name"] if item["name"].startswith("lesson_") else f"lesson_{_pad2(item['lesson_num'])}"
        folder = lesson_dir / safe_name
        if folder.exists():
            shutil.rmtree(folder)
        out_pdf = folder / f"{book_stem}_{safe_name}.pdf"
        _slice_pdf(source_pdf, item["start"], item["end"], out_pdf)
        meta = {
            "kind": "lesson",
            "name": safe_name,
            "start": item["start"],
            "end": item["end"],
            "source_pdf": str(Path(source_pdf).resolve()),
            "pdf": str(out_pdf.resolve()),
            "lesson_num": item["lesson_num"],
            "lesson_name": item["lesson_name"],
            "topic_num": item["topic_num"],
            "topic_name": item["topic_name"],
            "heading": item["heading"],
            "title": item["title"],
            "raw_heading": item["raw_heading"],
            "raw_title": item["raw_title"],
        }
        out_pdf.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _write_bundle_manifest(
    bundle_dir: Path,
    book_stem: str,
    topics: list[dict[str, Any]],
    lessons: list[dict[str, Any]],
) -> Path:
    list_topic = []
    for index, topic in enumerate(topics):
        item = _normalize_topic(topic, index)
        list_topic.append(
            {
                item["name"]: {
                    "start": item["start"],
                    "end": item["end"],
                    "heading": item["heading"],
                    "title": item["title"],
                }
            }
        )

    list_lesson = []
    for index, lesson in enumerate(lessons):
        item = _normalize_lesson(lesson, index)
        list_lesson.append(
            {
                item["name"]: {
                    "start": item["start"],
                    "end": item["end"],
                    "heading": item["heading"],
                    "title": item["title"],
                    "topic_num": item["topic_num"],
                    "topic_name": item["topic_name"],
                }
            }
        )

    manifest = {"offset": 0, "list_topic": list_topic, "list_lesson": list_lesson}
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bundle_dir / f"{book_stem}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def _group_lessons(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, str], int] = {}
    for lesson in lessons:
        key = (str(lesson.get("topic_num") or ""), str(lesson.get("topic_name") or ""))
        if key not in index_by_key:
            index_by_key[key] = len(groups)
            groups.append(
                {
                    "topic_num": key[0],
                    "topic_name": key[1],
                    "lessons": [],
                }
            )
        groups[index_by_key[key]]["lessons"].append(lesson)
    return groups


def _map_lessons_to_topics(
    approved_topics: list[dict[str, Any]],
    raw_lessons: list[dict[str, Any]],
    job_id: str,
) -> list[dict[str, Any]]:
    seen_raw_keys: set[tuple[int, int, str]] = set()
    lessons_out: list[dict[str, Any]] = []
    total = len(approved_topics)

    for topic_index, topic in enumerate(approved_topics):
        t_start = int(topic.get("start") or 1)
        t_end = int(topic.get("end") or t_start)
        topic_lessons: list[dict[str, Any]] = []

        for lesson in raw_lessons:
            l_start = int(lesson.get("start") or 0)
            l_end = int(lesson.get("end") or 0)
            raw_key = (l_start, l_end, str(lesson.get("name") or ""))
            if raw_key in seen_raw_keys:
                continue
            if l_end >= t_start and l_start <= t_end:
                seen_raw_keys.add(raw_key)
                topic_lessons.append(
                    _normalize_lesson(
                        {
                            **lesson,
                            "start": max(l_start, t_start),
                            "end": min(l_end, t_end),
                        },
                        len(lessons_out) + len(topic_lessons),
                        topic,
                    )
                )

        if not topic_lessons:
            topic_lessons.append(
                _normalize_lesson(
                    {
                        "name": f"lesson_{len(lessons_out) + 1:02d}",
                        "start": t_start,
                        "end": t_end,
                        "heading": topic.get("heading", ""),
                        "title": topic.get("title", ""),
                    },
                    len(lessons_out),
                    topic,
                )
            )

        lessons_out.extend(topic_lessons)
        pct = round((topic_index + 1) * 100 / total) if total else 100
        write_json(
            _workspace_file(job_id, "lessons_partial.json"),
            {
                "lessons": lessons_out,
                "grouped_by_topic": _group_lessons(lessons_out),
                "updated_at": utc_now_iso(),
            },
        )
        update_progress(
            job_id,
            status=JobStatus.extracting_lessons,
            stage="extracting_lessons",
            message=f"Đang ánh xạ bài học theo chủ đề {topic_index + 1}/{total}.",
            percent=pct,
            current=topic_index + 1,
            total=total,
        )
        _log(job_id, f"topic {topic_index + 1}/{total}: pages {t_start}-{t_end} -> {len(topic_lessons)} lessons")

    return lessons_out


def extract_lessons_for_job(job_id: str) -> None:
    try:
        ensure_lesson_preconditions(job_id)
        config = read_json(job_config_path(job_id))
        source_pdf = Path(config["source_pdf_path"])
        approved_payload = read_json(_workspace_file(job_id, "approved_topics.json"))
        state = read_json(_workspace_file(job_id, "extraction_state.json"))

        approved_topics = [
            _normalize_topic(topic, index)
            for index, topic in enumerate(_extract_items(approved_payload, "topics"))
        ]
        raw_lessons = _extract_items(state.get("raw_lessons", []), "lessons")
        book_stem = state.get("book_stem") or Path(source_pdf).stem
        bundle_dir = Path(state.get("bundle_path") or state.get("rebuilt_bundle_path") or job_workspace(job_id) / book_stem)

        update_job_state(job_id, status=JobStatus.extracting_lessons, stage="extracting_lessons")
        update_progress(
            job_id,
            status=JobStatus.extracting_lessons,
            stage="extracting_lessons",
            message="Chuẩn bị dữ liệu chủ đề đã duyệt...",
            percent=5,
            current=0,
            total=len(approved_topics),
        )
        _log(job_id, "start extraction")
        _log(job_id, f"approved_topic_count={len(approved_topics)}")
        _log(job_id, f"raw_lesson_count={len(raw_lessons)}")

        if not approved_topics:
            raise ValueError("approved_topics.json contains no topics.")
        if not raw_lessons:
            _log(job_id, "raw lessons missing; fallback lessons will be created from topic ranges")

        update_progress(
            job_id,
            status=JobStatus.extracting_lessons,
            stage="mapping_lessons",
            message="Đang ánh xạ bài học vào từng chủ đề...",
            percent=15,
            current=0,
            total=len(approved_topics),
        )
        lessons_out = _map_lessons_to_topics(approved_topics, raw_lessons, job_id)
        if not lessons_out:
            raise ValueError("No lessons produced from approved topics.")

        _log(job_id, "rebuild Topic/ started")
        update_progress(
            job_id,
            status=JobStatus.extracting_lessons,
            stage="rebuilding_topic_pdfs",
            message="Đang cắt lại PDF theo chủ đề đã duyệt...",
            percent=35,
            current=0,
            total=len(approved_topics),
        )
        _build_topic_pdfs(bundle_dir, book_stem, str(source_pdf), approved_topics)
        _log(job_id, f"rebuild Topic/ completed count={len(approved_topics)}")

        _log(job_id, "rebuild Lesson/ started")
        update_progress(
            job_id,
            status=JobStatus.extracting_lessons,
            stage="rebuilding_lesson_pdfs",
            message="Đang cắt PDF theo bài học...",
            percent=65,
            current=0,
            total=len(lessons_out),
        )
        _build_lesson_pdfs(bundle_dir, book_stem, str(source_pdf), lessons_out)
        _log(job_id, f"rebuild Lesson/ completed count={len(lessons_out)}")

        manifest_path = _write_bundle_manifest(bundle_dir, book_stem, approved_topics, lessons_out)
        _log(job_id, f"manifest rewrite completed path={manifest_path}")
        update_progress(
            job_id,
            status=JobStatus.extracting_lessons,
            stage="writing_lessons",
            message="Đang ghi dữ liệu bài học...",
            percent=90,
            current=len(lessons_out),
            total=len(lessons_out),
        )

        state["rebuilt_bundle_path"] = str(bundle_dir)
        state["bundle_path"] = str(bundle_dir)
        state["book_stem"] = book_stem
        state["lessons_count"] = len(lessons_out)
        state["updated_at"] = utc_now_iso()
        write_json(_workspace_file(job_id, "extraction_state.json"), state)

        payload = {
            "lessons": lessons_out,
            "grouped_by_topic": _group_lessons(lessons_out),
            "updated_at": utc_now_iso(),
        }
        write_json(_workspace_file(job_id, "lessons_partial.json"), payload)

        update_result(
            job_id,
            ok=True,
            status=JobStatus.reviewing_lessons,
            message="Đã trích xuất bài học, chờ duyệt.",
            data={
                "bundle_path": str(bundle_dir),
                "book_stem": book_stem,
                "lessons": lessons_out,
                "grouped_by_topic": payload["grouped_by_topic"],
            },
        )
        update_progress(
            job_id,
            status=JobStatus.reviewing_lessons,
            stage="reviewing_lessons",
            message="Đã trích xuất bài học, chờ duyệt.",
            percent=100,
            current=len(lessons_out),
            total=len(lessons_out),
        )
        update_job_state(job_id, status=JobStatus.reviewing_lessons, stage="reviewing_lessons")
        _log(job_id, f"success lessons={len(lessons_out)} bundle_path={bundle_dir}")
    except Exception as exc:
        error = str(exc)
        try:
            if is_all_keys_cooldown_error(exc):
                mark_waiting_for_gemini_cooldown(
                    job_id,
                    retry_stage="extracting_lessons",
                    percent=35,
                    exc=exc,
                )
                _log(job_id, "waiting_gemini_cooldown")
                return
            update_job_state(job_id, status=JobStatus.error, stage="extracting_lessons", error=error)
            update_progress(
                job_id,
                status=JobStatus.error,
                stage="extracting_lessons",
                message=error,
                percent=0,
            )
            update_result(
                job_id,
                ok=False,
                status=JobStatus.error,
                message="Lesson extraction failed.",
                error=error,
            )
            _log(job_id, f"failure error={error}")
        except Exception:
            pass


def _approved_topic_for_num(job_id: str, topic_num: Any) -> dict[str, Any]:
    approved_path = _workspace_file(job_id, "approved_topics.json")
    if not approved_path.exists():
        raise FileNotFoundError("approved_topics.json not found. Approve the topic before extracting lessons.")
    payload = read_json(approved_path)
    topics = _extract_items(payload, "topics")
    wanted = _topic_num_int(topic_num)
    for index, topic in enumerate(topics):
        item = _normalize_topic(topic, index)
        if _topic_num_int(item.get("topic_num")) == wanted and topic.get("approved"):
            return item
    raise ValueError(f"Topic {topic_num} is not approved.")


def _merge_topic_lessons(
    job_id: str,
    topic_num: Any,
    topic_lessons: list[dict[str, Any]],
    existing_lessons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    partial_path = _workspace_file(job_id, "lessons_partial.json")
    if existing_lessons is None:
        existing_lessons = []
        if partial_path.exists():
            existing_lessons = _extract_items(read_json(partial_path), "lessons")
    wanted = _topic_num_int(topic_num)
    remaining = [lesson for lesson in existing_lessons if _topic_num_int(lesson.get("topic_num")) != wanted]
    merged = remaining + topic_lessons
    groups = _group_lessons(merged)
    payload = {
        "selected_topic_num": str(wanted or topic_num),
        "lessons": merged,
        "topics": groups,
        "grouped_by_topic": groups,
        "updated_at": utc_now_iso(),
    }
    write_json(partial_path, payload)
    return payload


def extract_lessons_for_topic(job_id: str, topic_num: Any) -> None:
    try:
        ensure_lesson_preconditions(job_id)
        config = read_json(job_config_path(job_id))
        source_pdf = Path(config["source_pdf_path"])
        state = read_json(_workspace_file(job_id, "extraction_state.json"))
        topic = _approved_topic_for_num(job_id, topic_num)

        raw_lessons = _extract_items(state.get("raw_lessons", []), "lessons")
        book_stem = state.get("book_stem") or Path(source_pdf).stem
        bundle_dir = Path(state.get("bundle_path") or state.get("rebuilt_bundle_path") or job_workspace(job_id) / book_stem)
        padded = _pad2(topic.get("topic_num"))

        update_job_state(job_id, status=JobStatus.extracting_lessons, stage="extracting_lessons_for_topic")
        update_progress(
            job_id,
            status=JobStatus.extracting_lessons,
            stage="extracting_lessons_for_topic",
            message=f"Đang trích xuất bài học cho Topic {padded}...",
            percent=5,
            current=0,
            total=1,
        )
        _log(job_id, f"start per-topic extraction topic={padded}")

        previous_lessons = []
        lessons_partial_path = _workspace_file(job_id, "lessons_partial.json")
        if lessons_partial_path.exists():
            previous_lessons = _extract_items(read_json(lessons_partial_path), "lessons")

        lessons_out = _map_lessons_to_topics([topic], raw_lessons, job_id)
        if not lessons_out:
            raise ValueError(f"No lessons produced for Topic {padded}.")

        update_progress(
            job_id,
            status=JobStatus.extracting_lessons,
            stage="rebuilding_lesson_pdfs_for_topic",
            message=f"Đang cắt PDF bài học cho Topic {padded}...",
            percent=65,
            current=0,
            total=len(lessons_out),
        )
        _build_lesson_pdfs_for_topic(bundle_dir, book_stem, str(source_pdf), lessons_out)

        topic_dir = job_workspace(job_id) / "lessons" / f"topic_{padded}"
        topic_dir.mkdir(parents=True, exist_ok=True)
        topic_payload = {
            "topic_num": str(topic.get("topic_num")),
            "topic_name": topic.get("topic_name"),
            "lessons": lessons_out,
            "grouped_by_topic": _group_lessons(lessons_out),
            "updated_at": utc_now_iso(),
        }
        write_json(topic_dir / "lessons_partial.json", topic_payload)
        merged_payload = _merge_topic_lessons(job_id, topic.get("topic_num"), lessons_out, previous_lessons)

        state["rebuilt_bundle_path"] = str(bundle_dir)
        state["bundle_path"] = str(bundle_dir)
        state["book_stem"] = book_stem
        state["selected_topic_num"] = str(topic.get("topic_num"))
        extracted_topics = set(state.get("lesson_extracted_topic_nums") or [])
        extracted_topics.add(str(topic.get("topic_num")))
        state["lesson_extracted_topic_nums"] = sorted(extracted_topics, key=lambda value: int(value) if str(value).isdigit() else 999)
        state["lessons_count"] = len(merged_payload["lessons"])
        state["updated_at"] = utc_now_iso()
        write_json(_workspace_file(job_id, "extraction_state.json"), state)

        message = f"Đã trích xuất bài học cho Topic {padded}, chờ duyệt."
        update_result(
            job_id,
            ok=True,
            status=JobStatus.reviewing_lessons,
            message=message,
            data={
                "bundle_path": str(bundle_dir),
                "book_stem": book_stem,
                "selected_topic_num": str(topic.get("topic_num")),
                "lessons": lessons_out,
                "grouped_by_topic": topic_payload["grouped_by_topic"],
            },
        )
        update_progress(
            job_id,
            status=JobStatus.reviewing_lessons,
            stage="reviewing_lessons_for_topic",
            message=message,
            percent=100,
            current=len(lessons_out),
            total=len(lessons_out),
        )
        update_job_state(job_id, status=JobStatus.reviewing_lessons, stage="reviewing_lessons_for_topic")
        _log(job_id, f"success per-topic lessons topic={padded} count={len(lessons_out)}")
    except Exception as exc:
        error = str(exc)
        try:
            if is_all_keys_cooldown_error(exc):
                mark_waiting_for_gemini_cooldown(
                    job_id,
                    retry_stage="extracting_lessons_for_topic",
                    percent=35,
                    exc=exc,
                )
                _log(job_id, "waiting_gemini_cooldown")
                return
            update_job_state(job_id, status=JobStatus.error, stage="extracting_lessons_for_topic", error=error)
            update_progress(
                job_id,
                status=JobStatus.error,
                stage="extracting_lessons_for_topic",
                message=error,
                percent=0,
            )
            update_result(
                job_id,
                ok=False,
                status=JobStatus.error,
                message="Per-topic lesson extraction failed.",
                error=error,
            )
            _log(job_id, f"per-topic failure error={error}")
        except Exception:
            pass


def ensure_lesson_preconditions(job_id: str) -> None:
    ensure_job_exists(job_id)
    config = read_json(job_config_path(job_id))
    source_pdf = Path(config.get("source_pdf_path") or "")
    if not source_pdf.exists():
        raise FileNotFoundError(f"Source PDF not found: {source_pdf}")
    if not _workspace_file(job_id, "approved_topics.json").exists():
        raise FileNotFoundError("approved_topics.json not found. Approve topics before extracting lessons.")
    if not _workspace_file(job_id, "extraction_state.json").exists():
        raise FileNotFoundError("extraction_state.json not found. Topic extraction must run first.")


def read_lessons(job_id: str) -> dict[str, Any]:
    ensure_job_exists(job_id)
    approved_path = _workspace_file(job_id, "approved_lessons.json")
    partial_path = _workspace_file(job_id, "lessons_partial.json")
    if approved_path.exists():
        raw = read_json(approved_path)
        lessons = _extract_items(raw, "lessons")
        return {
            "ok": True,
            "job_id": job_id,
            "approved": bool(raw.get("approved_all", raw.get("approved", False))),
            "approved_all": bool(raw.get("approved_all", raw.get("approved", False))),
            "approved_lesson_nums": raw.get("approved_lesson_nums", []),
            "pending_lesson_nums": raw.get("pending_lesson_nums", []),
            "lessons": lessons,
            "grouped_by_topic": raw.get("grouped_by_topic") or _group_lessons(lessons),
            "raw": raw,
        }
    if partial_path.exists():
        raw = read_json(partial_path)
        lessons = _extract_items(raw, "lessons")
        return {
            "ok": True,
            "job_id": job_id,
            "approved": False,
            "lessons": lessons,
            "grouped_by_topic": raw.get("grouped_by_topic") or _group_lessons(lessons),
            "raw": raw,
        }
    raise FileNotFoundError("No lessons found for this job.")


def _lesson_num_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value or ""))
        return int(match.group(0)) if match else None


def _preview_roots(job_id: str) -> list[Path]:
    state_path = _workspace_file(job_id, "extraction_state.json")
    state = read_json(state_path) if state_path.exists() else {}
    roots: list[Path] = []
    for key in ["bundle_path", "rebuilt_bundle_path", "final_bundle_path"]:
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


def find_lesson_preview_pdf(job_id: str, lesson_num: Any) -> Path:
    ensure_job_exists(job_id)
    wanted = _lesson_num_int(lesson_num)
    checked: list[str] = []
    for root in _preview_roots(job_id):
        lesson_root = root / "Lesson"
        folder = lesson_root / f"lesson_{int(wanted or 0):02d}"
        checked.append(str(folder))
        candidates = sorted(folder.glob("*.pdf")) if folder.exists() else []
        if wanted is not None and lesson_root.exists():
            candidates.extend(sorted(lesson_root.glob(f"**/*lesson_{wanted:02d}*.pdf")))
            for meta_path in sorted(lesson_root.glob("**/*.json")):
                try:
                    meta = read_json(meta_path)
                except Exception:
                    continue
                if _lesson_num_int(meta.get("lesson_num")) == wanted:
                    candidates.extend(sorted(meta_path.parent.glob("*.pdf")))
        for pdf in candidates:
            if pdf.exists():
                return pdf
    raise FileNotFoundError(f"Lesson preview PDF not found for lesson_{lesson_num}. Checked: {checked[:12]}")


def _lesson_num_set(values: list[Any] | None) -> set[int]:
    nums: set[int] = set()
    for value in values or []:
        parsed = _topic_num_int(value)
        if parsed is not None:
            nums.add(parsed)
    return nums


def _load_lesson_approval_payload(job_id: str) -> dict[str, Any]:
    partial_lessons = []
    partial_path = _workspace_file(job_id, "lessons_partial.json")
    if partial_path.exists():
        partial_lessons = _extract_items(read_json(partial_path), "lessons")
    by_num = {_topic_num_int(lesson.get("lesson_num")): _normalize_lesson(lesson, index) for index, lesson in enumerate(partial_lessons)}
    approved_nums: set[int] = set()
    if _workspace_file(job_id, "approved_lessons.json").exists():
        raw = read_json(_workspace_file(job_id, "approved_lessons.json"))
        for index, lesson in enumerate(_extract_items(raw, "lessons")):
            item = _normalize_lesson(lesson, index)
            num = _topic_num_int(item.get("lesson_num"))
            if num is None:
                continue
            by_num[num] = {**by_num.get(num, {}), **item}
            if item.get("approved") or raw.get("approved") is True:
                approved_nums.add(num)
        approved_nums.update(_lesson_num_set(raw.get("approved_lesson_nums")))

    lessons = []
    for num in sorted(num for num in by_num if num is not None):
        lesson = by_num[num]
        lesson["approved"] = num in approved_nums
        lessons.append(lesson)
    pending = [num for num in sorted(by_num) if num is not None and num not in approved_nums]
    approved_all = bool(by_num) and not pending
    return {
        "approved_all": approved_all,
        "approved": approved_all,
        "approved_lesson_nums": sorted(approved_nums),
        "pending_lesson_nums": pending,
        "lessons": lessons,
        "grouped_by_topic": _group_lessons(lessons),
        "updated_at": utc_now_iso(),
    }


def save_lessons(job_id: str, lessons: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_job_exists(job_id)
    normalized = [_normalize_lesson(lesson, index) for index, lesson in enumerate(lessons)]
    payload = {
        "lessons": normalized,
        "grouped_by_topic": _group_lessons(normalized),
        "updated_at": utc_now_iso(),
    }
    write_json(_workspace_file(job_id, "lessons_partial.json"), payload)
    update_result(
        job_id,
        ok=True,
        status=JobStatus.reviewing_lessons,
        message="Lessons updated.",
        data=payload,
    )
    update_progress(
        job_id,
        status=JobStatus.reviewing_lessons,
        stage="reviewing_lessons",
        message="Lessons updated. Waiting for approval.",
        percent=100,
        current=len(normalized),
        total=len(normalized),
    )
    update_job_state(job_id, status=JobStatus.reviewing_lessons, stage="reviewing_lessons")
    _log(job_id, f"lessons updated count={len(normalized)}")
    return {"ok": True, "job_id": job_id, "approved": False, **payload}


def approve_lessons(
    job_id: str,
    lessons: list[dict[str, Any]] | None = None,
    lesson_nums: list[Any] | None = None,
) -> dict[str, Any]:
    ensure_job_exists(job_id)
    if lessons is not None:
        save_lessons(job_id, lessons)
    current = _load_lesson_approval_payload(job_id)
    all_lessons = current["lessons"]
    if not all_lessons:
        raise ValueError("Lesson list is empty.")
    selected_nums = _lesson_num_set(lesson_nums)
    if not selected_nums:
        selected_nums = {_topic_num_int(lesson.get("lesson_num")) for lesson in all_lessons}
        selected_nums = {num for num in selected_nums if num is not None}

    approved_nums = set(current["approved_lesson_nums"])
    approved_at = utc_now_iso()
    changed = 0
    for lesson in all_lessons:
        num = _topic_num_int(lesson.get("lesson_num"))
        if num not in selected_nums:
            continue
        update_progress(
            job_id,
            status=JobStatus.reviewing_lessons,
            stage="approving_lesson",
            message="Đang lưu bài học vào MongoDB...",
            percent=40,
        )
        summary = save_lesson_metadata_for_job(job_id, lesson)
        update_progress(
            job_id,
            status=JobStatus.reviewing_lessons,
            stage="approving_lesson",
            message="Đang tải PDF bài học lên MinIO...",
            percent=80,
        )
        lesson.update(
            {
                "approved": True,
                "approved_at": approved_at,
                "metadata_edu_saved": True,
                "minio_uploaded": True,
                "asset_object_key": summary["object_key"],
                "asset_url": summary["url"],
                "lesson_id": summary["lesson_id"],
                "asset_id": summary["asset_id"],
            }
        )
        approved_nums.add(num)
        changed += 1
    if changed == 0:
        raise ValueError("Selected lesson_nums were not found.")
    all_nums = [num for num in (_topic_num_int(lesson.get("lesson_num")) for lesson in all_lessons) if num is not None]
    pending = [num for num in sorted(all_nums) if num not in approved_nums]
    approved_all = bool(all_nums) and not pending
    payload = {
        "lessons": all_lessons,
        "grouped_by_topic": _group_lessons(all_lessons),
        "approved": approved_all,
        "approved_all": approved_all,
        "approved_lesson_nums": sorted(approved_nums),
        "pending_lesson_nums": pending,
        "approved_at": approved_at,
    }
    write_json(_workspace_file(job_id, "approved_lessons.json"), payload)
    update_job_state(job_id, status=JobStatus.reviewing_lessons, stage="reviewing_lessons")
    update_result(
        job_id,
        ok=True,
        status=JobStatus.reviewing_lessons,
        message="Đã duyệt bài học và lưu Metadata-Edu.",
        data=payload,
    )
    update_progress(
        job_id,
        status=JobStatus.reviewing_lessons,
        stage="reviewing_lessons",
        message="Đã duyệt bài học và lưu Metadata-Edu.",
        percent=100,
        current=len(approved_nums),
        total=len(all_nums),
    )
    _log(job_id, f"lessons approved selected={sorted(selected_nums)} approved_total={len(approved_nums)}")
    return {"ok": True, "job_id": job_id, "approved": approved_all, **payload}


def approve_lesson(job_id: str, lesson_num: Any) -> dict[str, Any]:
    return approve_lessons(job_id, lesson_nums=[lesson_num])
