from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any

from app.core.logging import append_job_log
from app.core.paths import job_config_path, job_log_path, job_workspace, output_root
from app.models.job_models import JobStatus
from app.services.job_service import ensure_job_exists, update_job_state
from app.services.kaggle_service import build_skipped_kaggle_result, kaggle_result_path, run_kaggle_postprocess_for_job
from app.services.chunk_metadata_service import save_final_chunks_after_kaggle
from app.services.keyword_service import ensure_keyword_placeholders, extract_keywords_for_book
from app.services.keyword_metadata_service import save_keyword_metadata_for_job
from app.services.progress_service import update_progress, update_result
from app.utils.files import ensure_dir, read_json, write_json
from app.utils.time import utc_now_iso


PATH_FIELDS = {
    "pdf",
    "pdf_path",
    "chunk_pdf",
    "source_lesson_pdf",
    "metadata_path",
}

BUNDLE_READABLE_STATUSES = {
    JobStatus.bundle_ready.value,
    JobStatus.mongodb_imported.value,
}


def _workspace_file(job_id: str, name: str) -> Path:
    return job_workspace(job_id) / name


def _bundle_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "bundle.log"


def _log(job_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}"
    append_job_log(_bundle_log_path(job_id), line)
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [bundle] {message}")


def _extract_items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get(key, payload.get("items", []))
    else:
        items = payload
    return [dict(item) for item in items or [] if isinstance(item, dict)]


def _find_workspace_bundle(job_id: str) -> tuple[dict[str, Any], str, Path]:
    state_path = _workspace_file(job_id, "extraction_state.json")
    if not state_path.exists():
        raise FileNotFoundError("extraction_state.json not found. Extraction stages must run first.")
    state = read_json(state_path)
    book_stem = state.get("book_stem")
    if not book_stem:
        raise FileNotFoundError("book_stem missing in extraction_state.json.")
    bundle_dir = Path(state.get("bundle_path") or state.get("rebuilt_bundle_path") or job_workspace(job_id) / book_stem)
    return state, book_stem, bundle_dir


def _output_bundle_dir(book_stem: str) -> Path:
    return output_root() / book_stem


def _manifest_path(bundle_dir: Path, book_stem: str) -> Path:
    return bundle_dir / f"{book_stem}.json"


def ensure_bundle_preconditions(job_id: str) -> tuple[str, Path]:
    ensure_job_exists(job_id)
    config = read_json(job_config_path(job_id))
    source_pdf = Path(config.get("source_pdf_path") or "")
    if not source_pdf.exists():
        raise FileNotFoundError(f"Source PDF not found: {source_pdf}")
    if not _workspace_file(job_id, "approved_chunks.json").exists():
        raise FileNotFoundError("approved_chunks.json not found. Approve chunks before preparing bundle.")
    _state, book_stem, bundle_dir = _find_workspace_bundle(job_id)
    if not bundle_dir.exists():
        raise FileNotFoundError(f"Workspace bundle not found: {bundle_dir}")
    for name in ["Topic", "Lesson", "Chunk"]:
        folder = bundle_dir / name
        if not folder.exists():
            raise FileNotFoundError(f"{name}/ not found in workspace bundle: {folder}")
    manifest = _manifest_path(bundle_dir, book_stem)
    if not manifest.exists():
        raise FileNotFoundError(f"Bundle manifest not found: {manifest}")
    return book_stem, bundle_dir


def _count_json_dirs(bundle_dir: Path, folder: str) -> int:
    root = bundle_dir / folder
    if not root.exists():
        return 0
    paths = [path for path in root.rglob("*.json") if not path.name.endswith(".keywords.json")]
    if folder == "Chunk":
        paths = [
            path
            for path in paths
            if path.parent.name.startswith("chunk_") and "_chunk_" in path.stem
        ]
    return len(paths)


def count_bundle_artifacts(bundle_dir: Path) -> dict[str, int]:
    return {
        "topics": _count_json_dirs(bundle_dir, "Topic"),
        "lessons": _count_json_dirs(bundle_dir, "Lesson"),
        "chunks": _count_json_dirs(bundle_dir, "Chunk"),
        "keyword_files": len(list((bundle_dir / "Chunk").rglob("*.keywords.json"))) if (bundle_dir / "Chunk").exists() else 0,
        "topic_pdfs": len(list((bundle_dir / "Topic").rglob("*.pdf"))) if (bundle_dir / "Topic").exists() else 0,
        "lesson_pdfs": len(list((bundle_dir / "Lesson").rglob("*.pdf"))) if (bundle_dir / "Lesson").exists() else 0,
        "chunk_pdfs": len(list((bundle_dir / "Chunk").rglob("*.pdf"))) if (bundle_dir / "Chunk").exists() else 0,
    }


def _validate_bundle(bundle_dir: Path, book_stem: str) -> list[str]:
    missing: list[str] = []
    manifest = _manifest_path(bundle_dir, book_stem)
    if not manifest.exists():
        missing.append(str(manifest))
    for name in ["Topic", "Lesson", "Chunk"]:
        folder = bundle_dir / name
        if not folder.exists():
            missing.append(str(folder))
    counts = count_bundle_artifacts(bundle_dir)
    for key in ["topic_pdfs", "lesson_pdfs", "chunk_pdfs"]:
        if counts[key] == 0:
            missing.append(f"{key}=0")
    if counts["chunks"] != counts["chunk_pdfs"]:
        missing.append(f"chunk_json_count={counts['chunks']} chunk_pdf_count={counts['chunk_pdfs']}")
    if counts["keyword_files"] != counts["chunk_pdfs"]:
        missing.append(f"keyword_files={counts['keyword_files']} chunk_pdf_count={counts['chunk_pdfs']}")
    return missing


def _rewrite_paths(value: Any, source_root: str, target_root: str) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_paths_for_key(key, item, source_root, target_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_paths(item, source_root, target_root) for item in value]
    if isinstance(value, str):
        return value.replace(source_root, target_root)
    return value


def _rewrite_paths_for_key(key: str, value: Any, source_root: str, target_root: str) -> Any:
    if key in PATH_FIELDS or isinstance(value, (dict, list)):
        return _rewrite_paths(value, source_root, target_root)
    if isinstance(value, str) and source_root in value:
        return value.replace(source_root, target_root)
    return value


def _rewrite_json_paths(bundle_dir: Path, source_bundle: Path, output_bundle: Path) -> None:
    source_root = str(source_bundle)
    target_root = str(output_bundle)
    for json_path in sorted(bundle_dir.rglob("*.json")):
        try:
            data = read_json(json_path)
        except Exception:
            continue
        if isinstance(data, dict):
            write_json(json_path, _rewrite_paths(data, source_root, target_root))


def _write_approved_chunk_metadata(job_id: str) -> None:
    approved = _extract_items(read_json(_workspace_file(job_id, "approved_chunks.json")), "chunks")
    for chunk in approved:
        metadata_path = chunk.get("metadata_path")
        if not metadata_path:
            continue
        path = Path(metadata_path)
        if not path.exists():
            continue
        existing = read_json(path)
        if isinstance(existing, dict):
            merged = {**existing, **chunk}
            write_json(path, merged)


def _read_existing_keyword_payloads(output_bundle: Path) -> dict[Path, dict[str, Any]]:
    payloads: dict[Path, dict[str, Any]] = {}
    if not output_bundle.exists():
        return payloads
    for keyword_path in sorted(output_bundle.rglob("*.keywords.json")):
        try:
            data = read_json(keyword_path)
        except Exception:
            continue
        if isinstance(data, dict):
            payloads[keyword_path.relative_to(output_bundle)] = data
    return payloads


def _restore_keyword_payloads(output_bundle: Path, payloads: dict[Path, dict[str, Any]]) -> int:
    restored = 0
    for relative_path, data in payloads.items():
        target = output_bundle / relative_path
        if target.exists():
            write_json(target, data)
            restored += 1
    return restored


def _copy_workspace_bundle(source_bundle: Path, output_bundle: Path) -> int:
    existing_keywords = _read_existing_keyword_payloads(output_bundle)
    if output_bundle.exists():
        shutil.rmtree(output_bundle)
    ensure_dir(output_bundle.parent)
    shutil.copytree(source_bundle, output_bundle)
    return _restore_keyword_payloads(output_bundle, existing_keywords)


def _write_bundle_summary(job_id: str, summary: dict[str, Any]) -> None:
    write_json(_workspace_file(job_id, "bundle_summary.json"), summary)


def build_bundle_summary(job_id: str, *, require_ready: bool = False) -> dict[str, Any]:
    ensure_job_exists(job_id)
    state = read_json(_workspace_file(job_id, "job_state.json"))
    extraction_state, book_stem, _workspace_bundle = _find_workspace_bundle(job_id)
    output_bundle = _output_bundle_dir(book_stem)
    manifest = _manifest_path(output_bundle, book_stem)
    if require_ready and state.get("status") not in BUNDLE_READABLE_STATUSES:
        raise RuntimeError(f"Bundle is not ready. Current status: {state.get('status')}.")
    if not output_bundle.exists():
        raise FileNotFoundError(f"Output bundle not found: {output_bundle}")
    counts = count_bundle_artifacts(output_bundle)
    missing = _validate_bundle(output_bundle, book_stem)
    kaggle: dict[str, Any]
    kaggle_path = kaggle_result_path(job_id)
    if kaggle_path.exists():
        try:
            kaggle = read_json(kaggle_path)
        except Exception:
            kaggle = {"enabled": None, "skipped": None, "status": "unknown", "error": "Could not read kaggle_result.json"}
    else:
        kaggle = {"enabled": False, "skipped": True, "status": "skipped"}
    return {
        "ok": len(missing) == 0,
        "job_id": job_id,
        "status": state.get("status"),
        "book_stem": book_stem,
        "bundle_path": str(output_bundle),
        "manifest_path": str(manifest),
        "counts": counts,
        "missing": missing,
        "kaggle": kaggle,
        "raw": {
            "extraction_state": extraction_state,
            "bundle_summary_path": str(_workspace_file(job_id, "bundle_summary.json")),
        },
    }


def prepare_bundle_for_job(
    job_id: str,
    *,
    skip_kaggle: bool = False,
    skip_keywords: bool = False,
    retry_failed_keywords_only: bool = False,
) -> None:
    try:
        book_stem, workspace_bundle = ensure_bundle_preconditions(job_id)
        config = read_json(job_config_path(job_id))
        enable_kaggle = bool(config.get("enable_kaggle", False))
        enable_keywords = bool(config.get("enable_keywords", True))
        output_bundle = _output_bundle_dir(book_stem)

        update_job_state(job_id, status=JobStatus.preparing_bundle, stage="preparing_bundle")
        update_progress(
            job_id,
            status=JobStatus.preparing_bundle,
            stage="preparing_bundle",
            message="Chuẩn bị bundle cuối...",
            percent=5,
            current=0,
            total=0,
        )
        _log(job_id, "bundle copy/rebuild started")
        _log(job_id, f"source_workspace_bundle={workspace_bundle}")
        _log(job_id, f"output_bundle={output_bundle}")

        _write_approved_chunk_metadata(job_id)
        update_progress(
            job_id,
            status=JobStatus.preparing_bundle,
            stage="copying_bundle",
            message="Đang sao chép dữ liệu đã duyệt vào thư mục output...",
            percent=25,
        )
        restored_keywords = _copy_workspace_bundle(workspace_bundle, output_bundle)
        _rewrite_json_paths(output_bundle, workspace_bundle, output_bundle)
        ensure_keyword_placeholders(output_bundle)
        counts = count_bundle_artifacts(output_bundle)
        _log(job_id, f"bundle copied counts={counts} restored_keyword_files={restored_keywords}")

        update_progress(
            job_id,
            status=JobStatus.preparing_bundle,
            stage="validating_bundle",
            message="Đang kiểm tra Topic/Lesson/Chunk trong bundle...",
            percent=55,
            current=counts["chunk_pdfs"],
            total=counts["chunk_pdfs"],
        )
        missing = _validate_bundle(output_bundle, book_stem)
        if missing:
            raise RuntimeError(f"Prepared bundle validation failed: {missing}")

        kaggle_summary: dict[str, Any]
        if skip_kaggle:
            kaggle_summary = build_skipped_kaggle_result(job_id, enabled=enable_kaggle, reason="skip_kaggle=true")
            _log(job_id, "Kaggle postprocess skipped skip_kaggle=true")
        elif enable_kaggle:
            update_job_state(job_id, status=JobStatus.running_kaggle, stage="running_kaggle")
            update_progress(
                job_id,
                status=JobStatus.running_kaggle,
                stage="running_kaggle",
                message="Đang xử lý Kaggle OCR/cutline...",
                percent=20,
                current=0,
                total=counts["chunk_pdfs"],
            )
            _log(job_id, "Kaggle postprocess started")
            kaggle_summary = run_kaggle_postprocess_for_job(job_id=job_id, book_stem=book_stem, bundle_path=output_bundle)
            _log(job_id, f"Kaggle postprocess completed summary={kaggle_summary}")
            counts = count_bundle_artifacts(output_bundle)
            missing = _validate_bundle(output_bundle, book_stem)
            if missing:
                raise RuntimeError(f"Bundle validation failed after Kaggle apply: {missing}")
            update_progress(
                job_id,
                status=JobStatus.preparing_bundle,
                stage="saving_final_chunks",
                message="Đã nhận kết quả Kaggle, đang lưu chunk cuối vào MongoDB/MinIO...",
                percent=62,
                current=0,
                total=counts["chunk_pdfs"],
            )
            chunk_finalize_summary = save_final_chunks_after_kaggle(job_id)
        else:
            kaggle_summary = build_skipped_kaggle_result(job_id, enabled=False, reason="job_config.enable_kaggle=false")
            _log(job_id, "Kaggle postprocess skipped enable_kaggle=false")
            chunk_finalize_summary = {"skipped": True, "reason": "job_config.enable_kaggle=false", "chunks_waiting_for_kaggle": True}

        if skip_kaggle:
            chunk_finalize_summary = {"skipped": True, "reason": "skip_kaggle=true", "chunks_waiting_for_kaggle": True}

        keyword_summary: dict[str, Any] | None = None
        if skip_keywords:
            keyword_summary = {
                "enabled": enable_keywords,
                "skipped_by_request": True,
                "retry_failed_keywords_only": retry_failed_keywords_only,
                "total_chunks": counts["chunk_pdfs"],
                "message": "Keyword extraction skipped for this prepare-bundle run.",
            }
            write_json(_workspace_file(job_id, "keyword_summary.json"), keyword_summary)
            _log(job_id, "keyword extraction skipped skip_keywords=true")
        elif enable_keywords:
            update_job_state(job_id, status=JobStatus.extracting_keywords, stage="extracting_keywords")
            update_progress(
                job_id,
                status=JobStatus.extracting_keywords,
                stage="extracting_keywords",
                message="Đang trích xuất keyword từ chunk cuối...",
                percent=70,
                current=0,
                total=counts["chunk_pdfs"],
            )
            if retry_failed_keywords_only:
                _log(job_id, "keyword extraction retry_failed_keywords_only=true")
            _log(job_id, "keyword extraction started")
            summary = extract_keywords_for_book(job_id=job_id, book_dir=output_bundle)
            keyword_summary = summary.to_dict()
            metadata_summary = save_keyword_metadata_for_job(job_id, output_bundle=output_bundle)
            keyword_summary["metadata_edu"] = metadata_summary
            write_json(_workspace_file(job_id, "keyword_summary.json"), keyword_summary)
            _log(job_id, f"keyword extraction completed summary={keyword_summary}")
        else:
            _log(job_id, "keyword extraction skipped enable_keywords=false")

        counts = count_bundle_artifacts(output_bundle)
        update_progress(
            job_id,
            status=JobStatus.preparing_bundle,
            stage="finalizing_bundle",
            message="Đang ghi manifest và tổng hợp kết quả bundle...",
            percent=90,
            current=counts["chunk_pdfs"],
            total=counts["chunk_pdfs"],
        )
        missing = _validate_bundle(output_bundle, book_stem)
        if missing:
            raise RuntimeError(f"Final bundle validation failed: {missing}")

        state = read_json(_workspace_file(job_id, "extraction_state.json"))
        state["final_bundle_path"] = str(output_bundle)
        state["final_manifest_path"] = str(_manifest_path(output_bundle, book_stem))
        state["updated_at"] = utc_now_iso()
        write_json(_workspace_file(job_id, "extraction_state.json"), state)

        update_job_state(job_id, status=JobStatus.bundle_ready, stage="bundle_ready")
        job_state = read_json(_workspace_file(job_id, "job_state.json"))
        job_state["output_path"] = str(output_bundle)
        job_state["updated_at"] = utc_now_iso()
        write_json(_workspace_file(job_id, "job_state.json"), job_state)
        result_data = {
            "book_stem": book_stem,
            "bundle_path": str(output_bundle),
            "manifest_path": str(_manifest_path(output_bundle, book_stem)),
            "counts": counts,
            "missing": missing,
            "kaggle": kaggle_summary,
            "chunk_finalize_summary": chunk_finalize_summary,
            "keyword_summary": keyword_summary,
        }
        _write_bundle_summary(job_id, {"ok": True, "job_id": job_id, "status": JobStatus.bundle_ready.value, **result_data})
        update_result(
            job_id,
            ok=True,
            status=JobStatus.bundle_ready,
            message="Bundle đã sẵn sàng.",
            data=result_data,
        )
        update_progress(
            job_id,
            status=JobStatus.bundle_ready,
            stage="bundle_ready",
            message="Bundle đã sẵn sàng.",
            percent=100,
            current=counts["chunk_pdfs"],
            total=counts["chunk_pdfs"],
        )
        _log(job_id, f"success bundle_ready path={output_bundle}")
    except Exception as exc:
        error = str(exc)
        try:
            update_job_state(job_id, status=JobStatus.error, stage="preparing_bundle", error=error)
            update_progress(job_id, status=JobStatus.error, stage="preparing_bundle", message=error, percent=0)
            update_result(job_id, ok=False, status=JobStatus.error, message="Bundle preparation failed.", error=error)
            _log(job_id, f"failure error={error}")
        except Exception:
            pass


def create_bundle_zip(job_id: str) -> Path:
    summary = build_bundle_summary(job_id, require_ready=True)
    bundle_path = Path(summary["bundle_path"])
    book_stem = summary["book_stem"]
    zip_path = job_workspace(job_id) / f"{book_stem}_bundle.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle_path.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle_path.parent))
    return zip_path
