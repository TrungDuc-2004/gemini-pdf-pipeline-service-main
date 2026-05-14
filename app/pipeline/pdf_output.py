from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from pypdf import PdfReader, PdfWriter

_log = logging.getLogger(__name__)


def _num_from_heading(heading: str) -> str:
    match = re.search(r"\d+", (heading or "").strip())
    return match.group(0) if match else ""


def _clean_name_upper_no_trailing_dots(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"(?:\s*\.)+\s*$", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.upper()


def prepare_workspace(
    pdf_path: str,
    output_root: str | Path,
    pdf_stem: str | None = None,
) -> dict[str, Path | str]:
    stem = pdf_stem or Path(pdf_path).stem
    root = Path(output_root)
    base_dir = root if root.name == stem else root / stem
    topic_dir = base_dir / "Topic"
    lesson_dir = base_dir / "Lesson"
    topic_dir.mkdir(parents=True, exist_ok=True)
    lesson_dir.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "base_dir": base_dir,
        "topic_dir": topic_dir,
        "lesson_dir": lesson_dir,
        "stem": stem,
    }


def save_manifest(base_dir: Path, pdf_stem: str, data: dict) -> Path:
    out_path = base_dir / f"{pdf_stem}.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _flatten_list_items(items: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    out = []
    for item in items or []:
        if not isinstance(item, dict) or len(item) != 1:
            continue
        name, value = next(iter(item.items()))
        if not isinstance(value, dict):
            continue
        start = value.get("start")
        end = value.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        heading = (value.get("heading") or "").strip()
        title = _clean_name_upper_no_trailing_dots(value.get("title") or "")
        num = _num_from_heading(heading)
        out.append(
            {
                "name": str(name),
                "start": start,
                "end": end,
                "num": num,
                "display_name": title,
                "heading": heading,
                "title": title,
                **{k: v for k, v in value.items() if k not in {"start", "end", "heading", "title"}},
            }
        )
    return out


def _flatten_start_printed_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in items or []:
        if not isinstance(item, dict) or len(item) != 1:
            continue
        name, value = next(iter(item.items()))
        if not isinstance(value, dict):
            continue
        start_printed = value.get("start_printed", value.get("start"))
        if not isinstance(start_printed, int) or start_printed < 1:
            continue
        heading = (value.get("heading") or "").strip()
        title = _clean_name_upper_no_trailing_dots(value.get("title") or "")
        out.append(
            {
                "name": str(name),
                "start_printed": start_printed,
                "num": _num_from_heading(heading),
                "heading": heading,
                "title": title,
                **{
                    k: v
                    for k, v in value.items()
                    if k not in {"start_printed", "start", "heading", "title"}
                },
            }
        )
    return out


def _sort_key_num_start(item: dict[str, Any], start_field: str) -> tuple[int, int]:
    number = item.get("num", "")
    return (int(number) if isinstance(number, str) and number.isdigit() else 999, item[start_field])


def _rebuild_manifest_list(items: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    out = []
    for index, item in enumerate(items, 1):
        key = item.get("name") or f"{prefix}_{index:02d}"
        out.append(
            {
                key: {
                    "start": item["start"],
                    "end": item["end"],
                    "heading": item.get("heading", ""),
                    "title": item.get("title", ""),
                }
            }
        )
    return out


def normalize_manifest(data: dict[str, Any], total_pages: int) -> dict[str, Any]:
    if "offset" not in data:
        return _normalize_from_start_end(data, total_pages)
    return _normalize_from_start_printed(data, total_pages)


def _normalize_from_start_printed(data: dict[str, Any], total_pages: int) -> dict[str, Any]:
    try:
        offset = int(data.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0

    try:
        main_end = int(data["printed_end_of_main"]) - 1
    except (KeyError, TypeError, ValueError):
        main_end = total_pages - offset

    topics = _flatten_start_printed_items(data.get("list_topic", []))
    lessons = _flatten_start_printed_items(data.get("list_lesson", []))
    topics.sort(key=lambda item: _sort_key_num_start(item, "start_printed"))
    lessons.sort(key=lambda item: _sort_key_num_start(item, "start_printed"))

    def dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        result = []
        for item in items:
            if item["start_printed"] in seen:
                _log.warning("Duplicate start_printed=%s skipped", item["start_printed"])
                continue
            seen.add(item["start_printed"])
            result.append(item)
        return result

    topics = dedup(topics)
    lessons = dedup(lessons)

    for index, topic in enumerate(topics):
        topic["end_printed"] = (
            topics[index + 1]["start_printed"] - 1 if index + 1 < len(topics) else main_end
        )

    topic_lesson_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for lesson in lessons:
        best = None
        for index, topic in enumerate(topics):
            if topic["start_printed"] <= lesson["start_printed"]:
                best = index
            else:
                break
        if best is not None:
            topic_lesson_map[best].append(lesson)

    for index, lesson in enumerate(lessons):
        lesson["end_printed"] = (
            lessons[index + 1]["start_printed"] - 1 if index + 1 < len(lessons) else main_end
        )

    for topic_index, topic in enumerate(topics):
        owned = topic_lesson_map.get(topic_index, [])
        if owned:
            max(owned, key=lambda item: item["start_printed"])["end_printed"] = topic["end_printed"]

    def to_pdf(item: dict[str, Any]) -> dict[str, Any]:
        start = max(1, min(item["start_printed"] + offset, total_pages))
        end = max(start, min(item["end_printed"] + offset, total_pages))
        return {**item, "start": start, "end": end}

    return {
        "list_topic": _rebuild_manifest_list([to_pdf(item) for item in topics], "topic"),
        "list_lesson": _rebuild_manifest_list([to_pdf(item) for item in lessons], "lesson"),
    }


def _normalize_from_start_end(data: dict[str, Any], total_pages: int) -> dict[str, Any]:
    topics = _flatten_list_items(data.get("list_topic", []), "topic")
    lessons = _flatten_list_items(data.get("list_lesson", []), "lesson")
    topics = [item for item in topics if 1 <= item["start"] <= item["end"] <= total_pages]
    lessons = [item for item in lessons if 1 <= item["start"] <= item["end"] <= total_pages]
    topics.sort(key=lambda item: _sort_key_num_start(item, "start"))
    lessons.sort(key=lambda item: _sort_key_num_start(item, "start"))
    return {
        "list_topic": _rebuild_manifest_list(topics, "topic"),
        "list_lesson": _rebuild_manifest_list(lessons, "lesson"),
    }


def split_pdf_by_ranges(
    src_pdf: str,
    ranges: Iterable[tuple[str, int, int]],
    out_dir: Path,
    pdf_stem: str,
) -> list[Path]:
    reader = PdfReader(src_pdf)
    total_pages = len(reader.pages)
    outputs = []
    for name, start, end in ranges:
        if start < 1 or end < 1 or start > end or start > total_pages:
            continue
        writer = PdfWriter()
        for page_index in range(start - 1, min(end, total_pages)):
            writer.add_page(reader.pages[page_index])
        safe_name = name.replace("/", "_").replace("\\", "_").strip()
        out_path = out_dir / f"{pdf_stem}_{safe_name}.pdf"
        out_dir.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as file:
            writer.write(file)
        outputs.append(out_path)
    return outputs


def split_pdf_item_to_folder(
    src_pdf: str,
    item: dict[str, Any],
    parent_dir: Path,
    pdf_stem: str,
    kind: str,
) -> Path | None:
    name = str(item["name"])
    start = int(item["start"])
    end = int(item["end"])
    folder = parent_dir / name.replace("/", "_").replace("\\", "_").strip()
    paths = split_pdf_by_ranges(src_pdf, [(name, start, end)], folder, pdf_stem)
    if not paths:
        return None
    pdf_path = paths[0]
    meta = {
        "kind": kind,
        "name": name,
        "start": start,
        "end": end,
        "source_pdf": str(Path(src_pdf).resolve()),
        "pdf": str(pdf_path.resolve()),
        "raw_heading": item.get("heading", ""),
        "raw_title": item.get("title", ""),
    }
    if kind == "topic":
        meta["topic_num"] = item.get("num", "")
        meta["topic_name"] = item.get("display_name", item.get("title", ""))
    else:
        meta["lesson_num"] = item.get("num", "")
        meta["lesson_name"] = item.get("display_name", item.get("title", ""))
    pdf_path.with_suffix(".json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return pdf_path


def split_from_manifest(
    src_pdf: str,
    data: dict[str, Any],
    base_dir: Path,
    pdf_stem: str | None = None,
) -> dict[str, list[str]]:
    stem = pdf_stem or Path(src_pdf).stem
    topic_dir = base_dir / "Topic"
    lesson_dir = base_dir / "Lesson"
    topic_dir.mkdir(parents=True, exist_ok=True)
    lesson_dir.mkdir(parents=True, exist_ok=True)

    result = {"topics": [], "lessons": []}
    for item in _flatten_list_items(data.get("list_topic", []), "topic"):
        path = split_pdf_item_to_folder(src_pdf, item, topic_dir, stem, "topic")
        if path:
            result["topics"].append(str(path))
    for item in _flatten_list_items(data.get("list_lesson", []), "lesson"):
        path = split_pdf_item_to_folder(src_pdf, item, lesson_dir, stem, "lesson")
        if path:
            result["lessons"].append(str(path))
    return result


def flatten_manifest_items(items: Any) -> list[dict[str, Any]]:
    out = []
    for item in items or []:
        if not isinstance(item, dict) or len(item) != 1:
            continue
        name, value = next(iter(item.items()))
        if not isinstance(value, dict):
            continue
        out.append(
            {
                "name": str(name),
                "start": value.get("start"),
                "end": value.get("end"),
                "heading": (value.get("heading") or "").strip(),
                "title": (value.get("title") or "").strip(),
                **{
                    k: v
                    for k, v in value.items()
                    if k not in {"start", "end", "heading", "title"}
                },
            }
        )
    return out

