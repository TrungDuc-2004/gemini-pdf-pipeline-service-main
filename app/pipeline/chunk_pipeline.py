from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.pipeline.gemini_runner import extract_structure_from_pdf
from app.pipeline.pdf_output import split_pdf_by_ranges
from app.pipeline.prompts import build_chunk_prompt_start_head

_VALID_HEADING_RE = re.compile(r"^\d+\.$")
_REJECT_TITLE_KEYWORDS = (
    "LUYỆN TẬP",
    "VẬN DỤNG",
    "BÀI TẬP",
    "CÂU HỎI",
    "NHIỆM VỤ",
    "HƯỚNG DẪN",
    "HOẠT ĐỘNG",
    "KHỞI ĐỘNG",
    "VÍ DỤ",
    "THỰC HÀNH",
    "TÓM TẮT",
    "ÔN TẬP",
    "TỔNG KẾT",
    "BƯỚC",
)
_SUB_ITEM_TITLE_RE = re.compile(r"^[a-zA-Z][.)]\s", re.IGNORECASE)
_SUB_ITEM_PAGE_RE = re.compile(r"\b[a-d][)]\s", re.IGNORECASE)
_EXERCISE_PAGE_RE = re.compile(
    r"(câu hỏi|bài tập|luyện tập|vận dụng|nhiệm vụ|hoạt động)",
    re.IGNORECASE,
)


def _is_junk_candidate(heading: str, title: str) -> tuple[bool, str]:
    heading = (heading or "").strip()
    title = (title or "").strip()
    title_upper = title.upper()
    if not _VALID_HEADING_RE.match(heading):
        return True, f"heading {heading!r} is not a numeric section heading"
    for keyword in _REJECT_TITLE_KEYWORDS:
        if title_upper == keyword or title_upper.startswith(keyword + " ") or title_upper.startswith(keyword + ":"):
            return True, f"title starts with forbidden keyword {keyword!r}"
    if _SUB_ITEM_TITLE_RE.match(title):
        return True, "title starts with a sub-item marker"
    return False, "ok"


def _extract_page_text(pdf_path: str, page_1based: int) -> str:
    try:
        reader = PdfReader(pdf_path)
        index = page_1based - 1
        if 0 <= index < len(reader.pages):
            return reader.pages[index].extract_text() or ""
    except Exception:
        pass
    return ""


def _heading_valid_in_page(page_text: str, heading: str, title: str) -> tuple[bool, str]:
    if not page_text.strip():
        return True, "no page text; skip validation"
    heading_num = heading.rstrip(".")
    lines = page_text.splitlines()
    heading_line_pattern = re.compile(r"^\s*" + re.escape(heading_num) + r"\s*[.]\s*\S")
    heading_line_index = -1
    for index, line in enumerate(lines):
        if heading_line_pattern.match(line):
            heading_line_index = index
            break
    if heading_line_index == -1:
        if _SUB_ITEM_PAGE_RE.search(page_text):
            return False, "heading not found as standalone line and page contains sub-items"
        return True, "heading not found but no sub-items on page; assume ok"
    before_text = "\n".join(lines[max(0, heading_line_index - 5) : heading_line_index])
    if _SUB_ITEM_PAGE_RE.search(before_text) and _EXERCISE_PAGE_RE.search(before_text):
        return False, "heading appears inside exercise/sub-item block"
    return True, f"heading found at line {heading_line_index}"


def _flatten_start_head(list_chunk: list[dict[str, dict[str, Any]]]) -> list[tuple[int, bool, str, str]]:
    out = []
    for item in list_chunk or []:
        if not isinstance(item, dict) or len(item) != 1:
            continue
        _name, obj = next(iter(item.items()))
        if not isinstance(obj, dict):
            continue
        start = obj.get("start")
        heading = obj.get("heading", "")
        title = obj.get("title", "")
        if isinstance(start, int) and isinstance(heading, str) and isinstance(title, str):
            out.append((start, bool(obj.get("content_head", False)), heading.strip(), title.strip()))
    out.sort(key=lambda item: item[0])
    return out


def _compute_chunks_from_start_head(
    items: list[tuple[int, bool, str, str]],
    total_pages: int,
) -> list[dict[str, dict[str, Any]]]:
    if total_pages < 1:
        return []
    if not items:
        return [
            {
                "chunk_01": {
                    "start": 1,
                    "end": total_pages,
                    "content_head": False,
                    "heading": "",
                    "title": "KHÔNG CÓ MỤC CHÍNH",
                }
            }
        ]

    fixed = []
    for index, (start, content_head, heading, title) in enumerate(items):
        start = max(1, min(start, total_pages))
        if index == 0:
            start = 1
            content_head = False
        fixed.append((start, content_head, heading.strip(), title.strip()))

    computed = []
    for index, (start, content_head, heading, title) in enumerate(fixed):
        if index + 1 < len(fixed):
            next_start, next_content_head, _next_heading, _next_title = fixed[index + 1]
            end = next_start if next_content_head else next_start - 1
            end = max(start, min(end, total_pages))
        else:
            end = total_pages
        computed.append(
            {
                f"chunk_{index + 1:02d}": {
                    "start": start,
                    "end": end,
                    "content_head": content_head,
                    "heading": heading,
                    "title": title,
                }
            }
        )
    return computed


def rebuild_lesson_chunks(
    *,
    lesson_pdf: Path,
    lesson_stem: str,
    chunk_root: Path,
    chunk_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    total_pages = len(PdfReader(str(lesson_pdf)).pages)
    lesson_chunk_dir = chunk_root / lesson_stem
    if lesson_chunk_dir.exists():
        shutil.rmtree(lesson_chunk_dir)
    lesson_chunk_dir.mkdir(parents=True, exist_ok=True)

    normalized = []
    for index, chunk in enumerate(chunk_items):
        start = max(1, min(int(chunk.get("start") or 1), total_pages))
        end = max(start, min(int(chunk.get("end") or start), total_pages))
        chunk_name = str(chunk.get("chunk") or chunk.get("chunk_num") or f"chunk_{index + 1:02d}")
        if not chunk_name.startswith("chunk_"):
            try:
                chunk_name = f"chunk_{int(chunk_name):02d}"
            except ValueError:
                chunk_name = f"chunk_{index + 1:02d}"
        normalized.append(
            {
                **chunk,
                "chunk": chunk_name,
                "start": start,
                "end": end,
                "content_head": bool(chunk.get("content_head", False)),
                "heading": (chunk.get("heading") or "").strip(),
                "title": (chunk.get("title") or chunk.get("chunk_name") or "").strip(),
            }
        )
    normalized.sort(key=lambda item: (item["start"], item["end"], item["chunk"]))

    result = []
    chunk_count = len(normalized)
    for index, chunk in enumerate(normalized):
        chunk_name = f"chunk_{index + 1:02d}"
        chunk_dir = lesson_chunk_dir / chunk_name
        chunk_dir.mkdir(parents=True, exist_ok=True)
        paths = split_pdf_by_ranges(
            src_pdf=str(lesson_pdf),
            ranges=[(chunk_name, chunk["start"], chunk["end"])],
            out_dir=chunk_dir,
            pdf_stem=lesson_stem,
        )
        if not paths:
            raise RuntimeError(f"Failed to split {lesson_stem} {chunk_name}")
        chunk_pdf_path = paths[0]
        meta = {
            **chunk,
            "source_lesson_pdf": str(lesson_pdf),
            "lesson_stem": lesson_stem,
            "chunk": chunk_name,
            "chunk_num": str(index + 1),
            "chunk_name": chunk.get("chunk_name") or chunk.get("title") or chunk_name,
            "chunk_pdf": str(chunk_pdf_path),
            "pdf_path": str(chunk_pdf_path),
            "metadata_path": str(chunk_pdf_path.with_suffix(".json")),
            "start": chunk["start"],
            "end": chunk["end"],
            "content_head": bool(chunk.get("content_head", False)),
            "heading": chunk.get("heading", ""),
            "title": chunk.get("title", ""),
            "total_pages": total_pages,
            "chunk_count": chunk_count,
        }
        if chunk_count == 1 and not meta["heading"].strip() and meta["title"].strip().upper() == "KHÔNG CÓ MỤC CHÍNH":
            meta["lesson_type"] = "thuc hanh"
        meta["chunk_id"] = f"{lesson_stem}:{chunk_name}"
        meta_path = chunk_pdf_path.with_suffix(".json")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        keyword_path = chunk_pdf_path.with_suffix(".keywords.json")
        if not keyword_path.exists():
            keyword_path.write_text(json.dumps({"keywords": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        result.append(meta)
    return result


def run_extract_and_split_chunks_for_book(
    key_manager,
    book_dir: str | Path,
    model: str,
    resume: bool = False,
    progress_cb=None,
    status_cb=None,
) -> dict[str, Any]:
    book_dir = Path(book_dir)
    lesson_dir = book_dir / "Lesson"
    chunk_root = book_dir / "Chunk"
    chunk_root.mkdir(parents=True, exist_ok=True)
    if not lesson_dir.exists():
        raise RuntimeError(f"Lesson directory not found: {lesson_dir}")
    lesson_pdfs = sorted(lesson_dir.rglob("*.pdf"))
    if not lesson_pdfs:
        raise RuntimeError(f"No lesson PDFs found under: {lesson_dir}")

    summary: dict[str, Any] = {
        "book_dir": str(book_dir),
        "lesson_count": len(lesson_pdfs),
        "chunk_pdf_files": [],
        "chunk_meta_files": [],
        "skipped_lessons": [],
    }

    for done, lesson_pdf in enumerate(lesson_pdfs, start=1):
        lesson_stem = lesson_pdf.stem
        if resume and (chunk_root / lesson_stem).exists() and any((chunk_root / lesson_stem).rglob("*.pdf")):
            summary["skipped_lessons"].append({"lesson": str(lesson_pdf), "reason": "existing chunks"})
            if progress_cb:
                progress_cb(done, len(lesson_pdfs), lesson_pdf)
            continue

        total_pages = len(PdfReader(str(lesson_pdf)).pages)
        raw = extract_structure_from_pdf(
            key_manager,
            str(lesson_pdf),
            build_chunk_prompt_start_head(total_pages=total_pages),
            model=model,
            status_cb=status_cb,
        )
        list_chunk_raw = raw.get("list_chunk")
        items = _flatten_start_head(list_chunk_raw) if isinstance(list_chunk_raw, list) else []

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
        chunks = []
        for item in computed:
            chunk_name, obj = next(iter(item.items()))
            chunks.append({"chunk": chunk_name, **obj})
        metas = rebuild_lesson_chunks(
            lesson_pdf=lesson_pdf,
            lesson_stem=lesson_stem,
            chunk_root=chunk_root,
            chunk_items=chunks,
        )
        for meta in metas:
            summary["chunk_pdf_files"].append(meta["chunk_pdf"])
            summary["chunk_meta_files"].append(meta["metadata_path"])

        if progress_cb:
            progress_cb(done, len(lesson_pdfs), lesson_pdf)

    return summary
