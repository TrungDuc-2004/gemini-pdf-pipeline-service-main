from __future__ import annotations

import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from app.core.gemini_keys import GeminiKeyManager
from app.pipeline.gemini_runner import extract_structure_from_pdf
from app.pipeline.pdf_output import (
    _flatten_start_printed_items,
    normalize_manifest,
    prepare_workspace,
    save_manifest,
    split_from_manifest,
)
from app.pipeline.prompts import build_topic_lesson_prompt, build_topic_verify_prompt


def _make_preview_first_pages(src_pdf: str, first_n_pages: int = 20) -> str:
    reader = PdfReader(src_pdf)
    page_count = min(max(1, first_n_pages), len(reader.pages))
    writer = PdfWriter()
    for index in range(page_count):
        writer.add_page(reader.pages[index])
    fd, tmp_path = tempfile.mkstemp(suffix=f"_preview_{page_count}p.pdf")
    os.close(fd)
    with open(tmp_path, "wb") as file:
        writer.write(file)
    return tmp_path


def _make_single_page_pdf(src_pdf: str, page_1based: int) -> str:
    reader = PdfReader(src_pdf)
    writer = PdfWriter()
    writer.add_page(reader.pages[page_1based - 1])
    fd, tmp_path = tempfile.mkstemp(suffix=f"_page{page_1based}.pdf")
    os.close(fd)
    with open(tmp_path, "wb") as file:
        writer.write(file)
    return tmp_path


def verify_topics_and_get_offset(
    key_manager: GeminiKeyManager,
    src_pdf: str,
    raw_data: dict[str, Any],
    total_pages: int,
    model: str,
    probe_radius: int = 3,
    progress_cb=None,
    status_cb=None,
) -> int:
    try:
        raw_offset = int(raw_data.get("offset", 0))
    except (TypeError, ValueError):
        raw_offset = 0

    topics = _flatten_start_printed_items(raw_data.get("list_topic", []))
    if not topics:
        if progress_cb:
            progress_cb(0, 0, f"Không có chủ đề để xác minh, dùng offset={raw_offset}")
        return raw_offset

    verified_offsets = []
    total = len(topics)
    if progress_cb:
        progress_cb(0, total, f"Bắt đầu xác minh {total} chủ đề")

    for index, topic in enumerate(topics):
        start_printed = topic["start_printed"]
        heading = topic.get("heading", "")
        title = topic.get("title", "")
        heading_base = heading.rstrip(".")
        label = f"{heading_base}: {title}" if title else heading_base
        predicted = start_printed + raw_offset
        candidates = range(max(1, predicted - probe_radius), min(total_pages, predicted + probe_radius) + 1)
        matched = None

        for candidate in candidates:
            tmp_path = _make_single_page_pdf(src_pdf, candidate)
            try:
                result = extract_structure_from_pdf(
                    key_manager,
                    tmp_path,
                    build_topic_verify_prompt(label),
                    model=model,
                    status_cb=status_cb,
                )
                if result.get("match") is True:
                    matched = candidate
                    break
            except Exception:
                pass
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        if matched is not None:
            offset = matched - start_printed
            verified_offsets.append(offset)
            if progress_cb:
                progress_cb(index + 1, total, f"{label}: trang={matched}, offset={offset}")
            if len(verified_offsets) >= 2 and verified_offsets[0] == verified_offsets[1]:
                return verified_offsets[0]
        elif progress_cb:
            progress_cb(index + 1, total, f"{label}: không tìm thấy trang khớp")

    if not verified_offsets:
        return raw_offset
    return Counter(verified_offsets).most_common(1)[0][0]


def run_extract_save_split(
    key_manager: GeminiKeyManager,
    pdf_path: str,
    model: str,
    output_root: str | Path,
    book_stem: str,
    progress_cb=None,
    status_cb=None,
) -> tuple[dict[str, Any], str, dict[str, list[str]]]:
    total_pages = len(PdfReader(str(pdf_path)).pages)
    prompt = (
        "QUAN TRỌNG: File PDF này chỉ là BẢN XEM TRƯỚC (preview) gồm 20 trang đầu để đọc MỤC LỤC.\n"
        "Hãy trả về offset, printed_end_of_main, và start_printed cho từng topic/lesson.\n\n"
        + build_topic_lesson_prompt()
    )

    preview_pdf = _make_preview_first_pages(pdf_path, first_n_pages=20)
    try:
        raw_data = extract_structure_from_pdf(
            key_manager,
            preview_pdf,
            prompt,
            model=model,
            status_cb=status_cb,
        )
    finally:
        try:
            os.remove(preview_pdf)
        except OSError:
            pass

    if progress_cb:
        progress_cb("verifying_topic_offsets", "Đang xác minh trang bắt đầu chủ đề")

    raw_data["offset"] = verify_topics_and_get_offset(
        key_manager,
        pdf_path,
        raw_data,
        total_pages,
        model=model,
        progress_cb=lambda current, total, message: progress_cb(
            "verifying_topic_offsets",
            message,
            current,
            total,
        )
        if progress_cb
        else None,
        status_cb=status_cb,
    )
    manifest = normalize_manifest(raw_data, total_pages=total_pages)
    workspace = prepare_workspace(pdf_path, output_root=output_root, pdf_stem=book_stem)
    base_dir = Path(workspace["base_dir"])
    json_path = save_manifest(base_dir, book_stem, manifest)
    split_result = split_from_manifest(pdf_path, manifest, base_dir, pdf_stem=book_stem)
    return manifest, str(json_path), split_result

