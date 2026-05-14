from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, validate_safe_mongo_db_name
from app.core.logging import append_job_log
from app.core.paths import job_config_path, job_log_path, job_workspace
from app.services.minio_service import upload_file
from app.services.subject_upload_service import grade_slug, safe_file_name, subject_slug
from app.services.topic_metadata_service import pad2, topic_import_key
from app.utils.files import read_json
from app.utils.time import utc_now_iso

USER_ID = "user_01"
PDF_CONTENT_TYPE = "application/pdf"


def lesson_type_from_name(name: Any) -> str:
    value = str(name or "").lower()
    return "thuc hanh" if "thực hành" in value or "thuc hanh" in value else "ly thuyet"


def lesson_asset_prefix(class_name: Any, subject_name: Any, topic_num: Any, lesson_num: Any) -> str:
    return f"documents/{grade_slug(class_name)}/{subject_slug(subject_name)}/lesson/topic_{pad2(topic_num)}-lesson_{pad2(lesson_num)}"


def lesson_import_key(class_name: Any, subject_name: Any, lesson_num: Any) -> str:
    return f"lesson/{grade_slug(class_name)}/{subject_slug(subject_name)}/lesson_{pad2(lesson_num)}"


def lesson_object_key(*, class_name: Any, subject_name: Any, book_stem: Any, topic_num: Any, lesson_num: Any) -> str:
    prefix = lesson_asset_prefix(class_name, subject_name, topic_num, lesson_num)
    return f"{prefix}/{safe_file_name(book_stem)}_lesson_{pad2(lesson_num)}.pdf"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_update(now: datetime) -> dict[str, Any]:
    return {"is_deleted": False, "deleted_at": None, "updated_at": now, "updated_by": USER_ID}


def _audit_insert(now: datetime) -> dict[str, Any]:
    return {"created_at": now, "created_by": USER_ID}


def _log(job_id: str, message: str) -> None:
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [lesson_metadata] {message}")


def _upsert_by_import_key(collection: Any, import_key: str, doc: dict[str, Any], now: datetime) -> Any:
    collection.update_one(
        {"import_key": import_key},
        {"$set": {**doc, "import_key": import_key, **_audit_update(now)}, "$setOnInsert": _audit_insert(now)},
        upsert=True,
    )
    found = collection.find_one({"import_key": import_key}, {"_id": 1})
    return found["_id"]


def _upsert_asset(collection: Any, doc: dict[str, Any], now: datetime) -> Any:
    collection.update_one(
        {"object_key": doc["object_key"]},
        {"$set": {**doc, **_audit_update(now)}, "$setOnInsert": _audit_insert(now)},
        upsert=True,
    )
    found = collection.find_one({"object_key": doc["object_key"]}, {"_id": 1})
    return found["_id"]


def _create_indexes(db: Any, job_id: str) -> None:
    for collection, key in [("lesson", "import_key"), ("asset", "object_key")]:
        try:
            db[collection].create_index(key, unique=True)
        except Exception as exc:
            _log(job_id, f"index_warning collection={collection} error={exc}")


def _find_lesson_pdf(job_id: str, lesson_num: Any) -> Path:
    state_path = job_workspace(job_id) / "extraction_state.json"
    if not state_path.exists():
        raise FileNotFoundError("extraction_state.json not found. Lesson extraction must run first.")

    state = read_json(state_path)
    bundle_dir = Path(state.get("bundle_path") or state.get("rebuilt_bundle_path") or "")
    if not bundle_dir.exists():
        raise FileNotFoundError(f"Bundle path not found: {bundle_dir}")

    target_num = int(re.search(r"\d+", str(lesson_num or "0")).group(0))
    lesson_id = f"lesson_{target_num:02d}"
    lesson_root = bundle_dir / "Lesson"

    candidates: list[Path] = []

    # Cách 1: tìm đúng folder lesson_07, lesson_08...
    lesson_folder = lesson_root / lesson_id
    if lesson_folder.exists():
        candidates.extend(sorted(lesson_folder.glob("*.pdf")))

    # Cách 2: tìm theo tên file có chứa lesson_07...
    if lesson_root.exists():
        candidates.extend(sorted(lesson_root.glob(f"**/*{lesson_id}*.pdf")))

    # Cách 3: tìm theo metadata .json.
    # Trường hợp PDF chỉ có 1 bài, folder có thể là lesson_01
    # nhưng metadata bên trong ghi lesson_num = 7.
    if lesson_root.exists():
        for meta_path in sorted(lesson_root.rglob("*.json")):
            try:
                meta = read_json(meta_path)
            except Exception:
                continue

            raw_num = meta.get("lesson_num")
            if raw_num is None:
                continue

            match = re.search(r"\d+", str(raw_num))
            if not match:
                continue

            meta_num = int(match.group(0))
            if meta_num == target_num:
                candidates.extend(sorted(meta_path.parent.glob("*.pdf")))

    # Cách 4: fallback nếu chỉ có đúng 1 lesson PDF trong bundle
    all_lesson_pdfs = sorted(lesson_root.rglob("*.pdf")) if lesson_root.exists() else []
    if len(all_lesson_pdfs) == 1:
        candidates.append(all_lesson_pdfs[0])

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if path.exists() and resolved not in seen:
            seen.add(resolved)
            unique.append(path)

    if not unique:
        raise FileNotFoundError(f"Lesson PDF not found for lesson_{target_num:02d}.")

    return unique[0]


def _topic_num(value: Any) -> Any:
    if value:
        return value
    match = re.search(r"\d+", str(value or ""))
    return match.group(0) if match else ""


def save_lesson_metadata_for_job(job_id: str, lesson: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    db_name = validate_safe_mongo_db_name(settings.mongo_db_name)
    config = read_json(job_config_path(job_id))
    state = read_json(job_workspace(job_id) / "extraction_state.json")

    class_name = config.get("class_name") or "11"
    subject_name = config.get("subject_name") or "Tin học"
    book_stem = state.get("book_stem") or config.get("book_name") or "document"
    topic_num = _topic_num(lesson.get("topic_num"))
    lesson_num = lesson.get("lesson_num")
    lesson_name = lesson.get("lesson_name") or lesson.get("title") or lesson.get("raw_title") or f"Lesson {lesson_num}"
    bucket = settings.minio_bucket
    prefix = lesson_asset_prefix(class_name, subject_name, topic_num, lesson_num)
    object_key = lesson_object_key(
        class_name=class_name,
        subject_name=subject_name,
        book_stem=book_stem,
        topic_num=topic_num,
        lesson_num=lesson_num,
    )
    source_pdf = _find_lesson_pdf(job_id, lesson_num)

    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
        db = client[db_name]
        _create_indexes(db, job_id)
        now = _now()
        t_key = topic_import_key(class_name, subject_name, topic_num)
        topic = db.topic.find_one({"import_key": t_key}, {"_id": 1})
        if not topic:
            raise FileNotFoundError(f"Parent topic not found in MongoDB: {t_key}")

        g_slug = grade_slug(class_name)
        s_slug = subject_slug(subject_name)
        import_key = lesson_import_key(class_name, subject_name, lesson_num)
        lesson_id = _upsert_by_import_key(
            db.lesson,
            import_key,
            {
                "lesson_num": lesson_num,
                "lesson_name": lesson_name,
                "lesson_category": "document",
                "lesson_type": lesson.get("lesson_type") or lesson_type_from_name(lesson_name),
                "topic_id": topic["_id"],
                "asset_prefixes": {
                    "documents": prefix,
                    "images": f"images/{g_slug}/{s_slug}/lesson/topic_{pad2(topic_num)}-lesson_{pad2(lesson_num)}",
                    "videos": f"videos/{g_slug}/{s_slug}/lesson/topic_{pad2(topic_num)}-lesson_{pad2(lesson_num)}",
                },
            },
            now,
        )
        uploaded = upload_file(source_pdf, bucket, object_key, PDF_CONTENT_TYPE)
        asset_id = _upsert_asset(
            db.asset,
            {
                "owner_type": "lesson",
                "owner_id": lesson_id,
                "asset_type": "document",
                "bucket": bucket,
                "path_prefix": prefix,
                "object_key": object_key,
                "file_name": Path(object_key).name,
                "url": uploaded["url"],
                "content_type": PDF_CONTENT_TYPE,
                "size": uploaded["size"],
            },
            now,
        )
        _log(job_id, f"lesson_saved db={db_name} import_key={import_key} object_key={object_key}")
        return {
            "lesson_id": str(lesson_id),
            "topic_id": str(topic["_id"]),
            "asset_id": str(asset_id),
            "bucket": bucket,
            "path_prefix": prefix,
            "object_key": object_key,
            "url": uploaded["url"],
            "size": uploaded["size"],
            "db_name": db_name,
        }
    finally:
        client.close()
