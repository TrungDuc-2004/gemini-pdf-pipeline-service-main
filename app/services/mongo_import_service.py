from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.core.config import get_settings, validate_safe_mongo_db_name
from app.core.logging import append_job_log
from app.core.paths import job_log_path, job_workspace, output_root
from app.models.job_models import JobStatus
from app.services.bundle_service import build_bundle_summary
from app.services.job_service import ensure_job_exists, update_job_state
from app.services.progress_service import update_progress, update_result
from app.utils.files import read_json, write_json
from app.utils.time import utc_now_iso


COLLECTIONS = {
    "class": "class",
    "subject": "subject",
    "topic": "topic",
    "lesson": "lesson",
    "chunk": "chunk",
    "keyword": "keyword",
    "chunk_keyword": "chunk_keyword",
    "import_job": "import_job",
}


def _workspace_file(job_id: str, name: str) -> Path:
    return job_workspace(job_id) / name


def _mongo_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "mongo_import.log"


def _log(job_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}"
    append_job_log(_mongo_log_path(job_id), line)
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [mongo_import] {message}")


def _safe_slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unknown"


def _num(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value)
    match = re.search(r"\d+", text)
    return match.group(0) if match else (text.strip() or fallback)


def _extract_single_item(obj: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if len(obj) == 1:
        key = next(iter(obj))
        value = obj[key]
        if isinstance(value, dict):
            return key, dict(value)
    return str(obj.get("name") or obj.get("id") or ""), dict(obj)


def _load_manifest_items(manifest: dict[str, Any], key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in manifest.get(key) or []:
        if isinstance(item, dict):
            name, data = _extract_single_item(item)
            if name and "name" not in data:
                data["name"] = name
            out.append(data)
    return out


def _json_files(root: Path, kind: str) -> list[Path]:
    paths = []
    for path in sorted(root.rglob("*.json")):
        if path.name.endswith(".keywords.json"):
            continue
        if kind == "chunk" and not (path.parent.name.startswith("chunk_") and "_chunk_" in path.stem):
            continue
        if kind in {"topic", "lesson"} and path.name in {"topic_meta.json", "lesson_meta.json"}:
            continue
        paths.append(path)
    return paths


def _load_json_optional(path: Path) -> dict[str, Any] | None:
    try:
        data = read_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _find_pdf_sibling(json_path: Path) -> str | None:
    pdfs = sorted(json_path.parent.glob("*.pdf"))
    return str(pdfs[0]) if pdfs else None


def _topic_num_from_name(name: str) -> str:
    return _num(name.replace("topic_", ""), "")


def _lesson_num_from_name(name: str) -> str:
    return _num(name.replace("lesson_", ""), "")


def _chunk_num_from_name(name: str) -> str:
    return _num(name.replace("chunk_", ""), "")


def _read_bundle(job_id: str) -> tuple[str, Path, dict[str, Any], dict[str, Any]]:
    status_path = _workspace_file(job_id, "job_state.json")
    state = read_json(status_path)
    if state.get("status") not in {JobStatus.bundle_ready.value, JobStatus.mongodb_imported.value}:
        raise RuntimeError(f"Job must be bundle_ready or mongodb_imported. Current status: {state.get('status')}.")
    summary = build_bundle_summary(job_id, require_ready=False)
    book_stem = summary["book_stem"]
    bundle_path = Path(summary["bundle_path"])
    manifest_path = bundle_path / f"{book_stem}.json"
    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle path not found: {bundle_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    manifest = read_json(manifest_path)
    return book_stem, bundle_path, manifest, state


def _topic_docs(book_stem: str, bundle_path: Path, manifest: dict[str, Any], subject_id: Any) -> list[dict[str, Any]]:
    manifest_by_num = {}
    for item in _load_manifest_items(manifest, "list_topic"):
        topic_num = str(item.get("topic_num") or _topic_num_from_name(item.get("name", "")))
        if topic_num:
            manifest_by_num[topic_num] = item

    docs = []
    for json_path in _json_files(bundle_path / "Topic", "topic"):
        meta = _load_json_optional(json_path)
        if not meta:
            continue
        topic_num = str(meta.get("topic_num") or _topic_num_from_name(meta.get("name", json_path.parent.name)))
        merged = {**manifest_by_num.get(topic_num, {}), **meta}
        topic_name = merged.get("topic_name") or merged.get("title") or merged.get("raw_title") or ""
        docs.append(
            {
                "topic_num": topic_num,
                "topic_name": topic_name,
                "topic_category": "document",
                "subject_id": subject_id,
                "import_key": f"topic:{book_stem}:{topic_num}",
                "pdf_path": str(merged.get("pdf") or merged.get("pdf_path") or _find_pdf_sibling(json_path) or ""),
                "metadata_path": str(json_path),
                "raw": merged,
            }
        )
    docs.sort(key=lambda item: int(_num(item["topic_num"], "0")))
    return docs


def _lesson_docs(book_stem: str, bundle_path: Path, manifest: dict[str, Any], topic_ids: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_by_num = {}
    for item in _load_manifest_items(manifest, "list_lesson"):
        lesson_num = str(item.get("lesson_num") or _lesson_num_from_name(item.get("name", "")))
        if lesson_num:
            manifest_by_num[lesson_num] = item

    docs = []
    for json_path in _json_files(bundle_path / "Lesson", "lesson"):
        meta = _load_json_optional(json_path)
        if not meta:
            continue
        lesson_num = str(meta.get("lesson_num") or _lesson_num_from_name(meta.get("name", json_path.parent.name)))
        merged = {**manifest_by_num.get(lesson_num, {}), **meta}
        topic_num = str(merged.get("topic_num") or "")
        lesson_name = merged.get("lesson_name") or merged.get("title") or merged.get("raw_title") or ""
        docs.append(
            {
                "lesson_num": lesson_num,
                "lesson_name": lesson_name,
                "lesson_category": "document",
                "topic_id": topic_ids.get(topic_num),
                "topic_num": topic_num,
                "import_key": f"lesson:{book_stem}:{lesson_num}",
                "lesson_type": merged.get("lesson_type"),
                "pdf_path": str(merged.get("pdf") or merged.get("pdf_path") or _find_pdf_sibling(json_path) or ""),
                "metadata_path": str(json_path),
                "raw": merged,
            }
        )
    docs.sort(key=lambda item: int(_num(item["lesson_num"], "0")))
    return docs


def _chunk_docs(book_stem: str, bundle_path: Path, lesson_ids_by_stem: dict[str, Any], lesson_ids_by_num: dict[str, Any]) -> list[dict[str, Any]]:
    docs = []
    for json_path in _json_files(bundle_path / "Chunk", "chunk"):
        meta = _load_json_optional(json_path)
        if not meta:
            continue
        lesson_stem = str(meta.get("lesson_stem") or json_path.parent.parent.name)
        lesson_num = str(meta.get("lesson_num") or _lesson_num_from_name(lesson_stem))
        chunk_num = str(meta.get("chunk_num") or _chunk_num_from_name(meta.get("chunk") or json_path.parent.name))
        title = meta.get("title") or meta.get("chunk_name") or ""
        docs.append(
            {
                "chunk_num": chunk_num,
                "chunk_name": meta.get("chunk_name") or title or f"chunk_{int(_num(chunk_num, '1')):02d}",
                "chunk_category": "document",
                "chunk_type": meta.get("chunk_type"),
                "lesson_id": lesson_ids_by_stem.get(lesson_stem) or lesson_ids_by_num.get(lesson_num),
                "lesson_num": lesson_num,
                "lesson_stem": lesson_stem,
                "heading": meta.get("heading"),
                "title": title,
                "start": meta.get("start"),
                "end": meta.get("end"),
                "content_head": bool(meta.get("content_head", False)),
                "import_key": f"chunk:{book_stem}:{lesson_stem}:{chunk_num}",
                "pdf_path": str(meta.get("pdf_path") or meta.get("chunk_pdf") or _find_pdf_sibling(json_path) or ""),
                "metadata_path": str(json_path),
                "keyword_path": str(json_path.with_suffix(".keywords.json")),
                "raw": meta,
            }
        )
    docs.sort(key=lambda item: (item["lesson_stem"], int(_num(item["chunk_num"], "0"))))
    return docs


def _extract_keywords_payload(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    if data.get("error"):
        return []
    raw = data.get("keywords")
    if raw is None and isinstance(data.get("data"), dict):
        raw = data["data"].get("keywords")
    if not isinstance(raw, list) or not raw:
        return []

    keywords = []
    for item in raw:
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = item.get("keyword") or item.get("keyword_name") or item.get("name") or item.get("text")
        else:
            value = None
        if isinstance(value, str) and value.strip():
            keywords.append(value.strip())
    deduped = []
    seen = set()
    for keyword in keywords:
        slug = _safe_slug(keyword)
        if slug not in seen:
            seen.add(slug)
            deduped.append(keyword)
    return deduped


def _mongo_client():
    from pymongo import MongoClient

    settings = get_settings()
    return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)


def _apply_upsert_stats(result: Any, counts: dict[str, int]) -> None:
    if result.upserted_id is not None:
        counts["inserted_count"] += 1
        counts["upserted_count"] += 1
    elif result.matched_count:
        counts["updated_count"] += 1


def _upsert(collection: Any, import_key: str, doc: dict[str, Any], now: str, counts: dict[str, int]) -> Any:
    payload = {**doc, "import_key": import_key, "is_deleted": False, "updated_at": now}
    result = collection.update_one(
        {"import_key": import_key},
        {"$set": payload, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    _apply_upsert_stats(result, counts)
    found = collection.find_one({"import_key": import_key}, {"_id": 1})
    return found["_id"]


def _safe_db_name() -> str:
    return validate_safe_mongo_db_name(get_settings().mongo_db_name)


def import_bundle_to_mongodb(job_id: str) -> dict[str, Any]:
    ensure_job_exists(job_id)
    started_at = utc_now_iso()
    errors: list[dict[str, Any]] = []
    counts = {
        "class_count": 0,
        "subject_count": 0,
        "topic_count": 0,
        "lesson_count": 0,
        "chunk_count": 0,
        "keyword_count": 0,
        "chunk_keyword_count": 0,
        "skipped_keyword_files": 0,
        "error_keyword_files": 0,
        "inserted_count": 0,
        "updated_count": 0,
        "upserted_count": 0,
    }

    try:
        book_stem, bundle_path, manifest, state = _read_bundle(job_id)
        update_job_state(job_id, status=JobStatus.importing_mongodb, stage="importing_mongodb")
        update_progress(
            job_id,
            status=JobStatus.importing_mongodb,
            stage="importing_mongodb",
            message="Importing bundle to MongoDB.",
            percent=0,
            current=0,
            total=0,
        )
        _log(job_id, f"bundle_path={bundle_path}")
        _log(job_id, f"mongo_db={_safe_db_name()}")

        config = read_json(_workspace_file(job_id, "job_config.json"))
        class_name = str(config.get("class_name") or "")
        subject_name = str(config.get("subject_name") or "")
        subject_type = str(config.get("subject_type") or "")
        now = utc_now_iso()

        client = _mongo_client()
        client.admin.command("ping")
        db = client[_safe_db_name()]

        for collection_name in COLLECTIONS.values():
            db[collection_name].create_index("import_key", unique=True)

        import_key = f"import_job:{job_id}"
        db.import_job.update_one(
            {"import_key": import_key},
            {
                "$set": {
                    "job_id": job_id,
                    "book_stem": book_stem,
                    "bundle_path": str(bundle_path),
                    "status": "running",
                    "errors": [],
                    "started_at": started_at,
                    "updated_at": now,
                    "import_key": import_key,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

        class_key = f"class:{class_name}"
        class_id = _upsert(
            db[COLLECTIONS["class"]],
            class_key,
            {"class_name": class_name},
            now,
            counts,
        )
        counts["class_count"] = 1

        subject_key = f"subject:{class_name}:{subject_name}:{subject_type}"
        subject_id = _upsert(
            db[COLLECTIONS["subject"]],
            subject_key,
            {
                "subject_name": subject_name,
                "subject_category": "document",
                "subject_type": subject_type,
                "class_id": class_id,
            },
            now,
            counts,
        )
        counts["subject_count"] = 1

        topic_ids: dict[str, Any] = {}
        for topic in _topic_docs(book_stem, bundle_path, manifest, subject_id):
            topic_id = _upsert(db[COLLECTIONS["topic"]], topic["import_key"], topic, now, counts)
            topic_ids[str(topic.get("topic_num") or "")] = topic_id
            counts["topic_count"] += 1

        lesson_ids_by_num: dict[str, Any] = {}
        lesson_ids_by_stem: dict[str, Any] = {}
        for lesson in _lesson_docs(book_stem, bundle_path, manifest, topic_ids):
            lesson_id = _upsert(db[COLLECTIONS["lesson"]], lesson["import_key"], lesson, now, counts)
            lesson_num = str(lesson.get("lesson_num") or "")
            lesson_ids_by_num[lesson_num] = lesson_id
            lesson_ids_by_stem[f"{book_stem}_lesson_{int(_num(lesson_num, '0')):02d}"] = lesson_id
            counts["lesson_count"] += 1

        chunk_ids: dict[str, Any] = {}
        for chunk in _chunk_docs(book_stem, bundle_path, lesson_ids_by_stem, lesson_ids_by_num):
            chunk_id = _upsert(db[COLLECTIONS["chunk"]], chunk["import_key"], chunk, now, counts)
            chunk_ids[chunk["import_key"]] = chunk_id
            counts["chunk_count"] += 1

            keyword_path = Path(chunk["keyword_path"])
            keyword_data = _load_json_optional(keyword_path)
            if keyword_data and keyword_data.get("error"):
                counts["error_keyword_files"] += 1
            keywords = _extract_keywords_payload(keyword_data)
            if not keywords:
                counts["skipped_keyword_files"] += 1
                continue

            for keyword_name in keywords:
                keyword_slug = _safe_slug(keyword_name)
                keyword_key = f"keyword:{keyword_slug}"
                keyword_id = _upsert(
                    db[COLLECTIONS["keyword"]],
                    keyword_key,
                    {
                        "keyword_name": keyword_name,
                        "keyword_slug": keyword_slug,
                        "aliases": [],
                    },
                    now,
                    counts,
                )
                counts["keyword_count"] += 1
                relation_key = f"chunk_keyword:{chunk['import_key']}:{keyword_slug}"
                _upsert(
                    db[COLLECTIONS["chunk_keyword"]],
                    relation_key,
                    {
                        "chunk_id": chunk_id,
                        "keyword_id": keyword_id,
                    },
                    now,
                    counts,
                )
                counts["chunk_keyword_count"] += 1

        completed_at = utc_now_iso()
        result = {
            "ok": True,
            "job_id": job_id,
            "status": JobStatus.mongodb_imported.value,
            "book_stem": book_stem,
            "bundle_path": str(bundle_path),
            "db_name": get_settings().mongo_db_name,
            "counts": counts,
            "skipped_keywords": counts["skipped_keyword_files"],
            "errors": errors,
            "started_at": started_at,
            "completed_at": completed_at,
        }
        write_json(_workspace_file(job_id, "mongo_import_result.json"), result)
        db.import_job.update_one(
            {"import_key": import_key},
            {
                "$set": {
                    "status": "completed",
                    "counts": counts,
                    "errors": errors,
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                }
            },
            upsert=True,
        )
        update_job_state(job_id, status=JobStatus.mongodb_imported, stage="mongodb_imported")
        update_progress(
            job_id,
            status=JobStatus.mongodb_imported,
            stage="mongodb_imported",
            message="MongoDB import completed.",
            percent=100,
            current=counts["chunk_count"],
            total=counts["chunk_count"],
        )
        update_result(
            job_id,
            ok=True,
            status=JobStatus.mongodb_imported,
            message="MongoDB import completed.",
            data=result,
        )
        _log(job_id, f"success counts={counts}")
        client.close()
        return result
    except Exception as exc:
        error = str(exc)
        completed_at = utc_now_iso()
        errors.append({"error": error, "at": completed_at})
        try:
            write_json(
                _workspace_file(job_id, "mongo_import_result.json"),
                {
                    "ok": False,
                    "job_id": job_id,
                    "status": JobStatus.error.value,
                    "counts": counts,
                    "errors": errors,
                    "started_at": started_at,
                    "completed_at": completed_at,
                },
            )
            update_job_state(job_id, status=JobStatus.error, stage="importing_mongodb", error=error)
            update_progress(job_id, status=JobStatus.error, stage="importing_mongodb", message=error, percent=0)
            update_result(job_id, ok=False, status=JobStatus.error, message="MongoDB import failed.", error=error)
            _log(job_id, f"failure error={error}")
        except Exception:
            pass
        raise


def read_mongo_import_result(job_id: str) -> dict[str, Any]:
    ensure_job_exists(job_id)
    path = _workspace_file(job_id, "mongo_import_result.json")
    if not path.exists():
        raise FileNotFoundError("mongo_import_result.json not found.")
    return read_json(path)
