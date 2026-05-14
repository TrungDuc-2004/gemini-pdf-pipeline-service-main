from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, validate_safe_mongo_db_name
from app.core.logging import append_job_log
from app.core.paths import job_config_path, job_log_path, job_workspace
from app.services.minio_service import upload_file
from app.services.subject_upload_service import grade_slug, safe_file_name, subject_slug
from app.utils.files import read_json
from app.utils.time import utc_now_iso


USER_ID = "user_01"
PDF_CONTENT_TYPE = "application/pdf"


def pad2(value: Any) -> str:
    try:
        return f"{int(value):02d}"
    except (TypeError, ValueError):
        return "00"


def topic_asset_prefix(class_name: Any, subject_name: Any, topic_num: Any) -> str:
    return f"documents/{grade_slug(class_name)}/{subject_slug(subject_name)}/topic/topic_{pad2(topic_num)}"


def topic_import_key(class_name: Any, subject_name: Any, topic_num: Any) -> str:
    return f"topic/{grade_slug(class_name)}/{subject_slug(subject_name)}/topic_{pad2(topic_num)}"


def topic_object_key(*, class_name: Any, subject_name: Any, book_stem: Any, topic_num: Any) -> str:
    prefix = topic_asset_prefix(class_name, subject_name, topic_num)
    return f"{prefix}/{safe_file_name(book_stem)}_topic_{pad2(topic_num)}.pdf"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_update(now: datetime) -> dict[str, Any]:
    return {
        "is_deleted": False,
        "deleted_at": None,
        "updated_at": now,
        "updated_by": USER_ID,
    }


def _audit_insert(now: datetime) -> dict[str, Any]:
    return {
        "created_at": now,
        "created_by": USER_ID,
    }


def _log(job_id: str, message: str) -> None:
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [topic_metadata] {message}")


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
    for collection, key in [("class", "import_key"), ("subject", "import_key"), ("topic", "import_key"), ("asset", "object_key")]:
        try:
            db[collection].create_index(key, unique=True)
        except Exception as exc:
            _log(job_id, f"index_warning collection={collection} error={exc}")


def _class_display_name(class_name: Any) -> str:
    text = str(class_name or "").strip()
    if text and not text.lower().startswith("lớp") and not text.lower().startswith("lop"):
        return f"Lớp {text}"
    return text or "Lớp"


def _find_topic_pdf(job_id: str, topic_num: Any) -> Path:
    state_path = job_workspace(job_id) / "extraction_state.json"
    if not state_path.exists():
        raise FileNotFoundError("extraction_state.json not found. Topic extraction must run first.")

    state = read_json(state_path)
    bundle_dir = Path(state.get("bundle_path") or state.get("rebuilt_bundle_path") or "")
    if not bundle_dir.exists():
        raise FileNotFoundError(f"Bundle path not found: {bundle_dir}")

    topic_id = f"topic_{pad2(topic_num)}"
    topic_root = bundle_dir / "Topic"

    candidates: list[Path] = []

    # Cách 1: tìm đúng thư mục topic_03, topic_04...
    topic_folder = topic_root / topic_id
    if topic_folder.exists():
        candidates.extend(sorted(topic_folder.glob("*.pdf")))

    # Cách 2: tìm theo tên file có chứa topic_03...
    if topic_root.exists():
        candidates.extend(sorted(topic_root.glob(f"**/*{topic_id}*.pdf")))

    # Cách 3: tìm theo metadata .json.
    # Trường hợp PDF chỉ có 1 chủ đề: folder có thể là topic_01,
    # nhưng metadata bên trong ghi topic_num = 3.
    if topic_root.exists():
        for meta_path in sorted(topic_root.rglob("*.json")):
            try:
                meta = read_json(meta_path)
            except Exception:
                continue

            try:
                meta_num = int(meta.get("topic_num"))
                target_num = int(topic_num)
            except Exception:
                continue

            if meta_num == target_num:
                candidates.extend(sorted(meta_path.parent.glob("*.pdf")))

    # Cách 4: fallback theo extraction_state.topic_pdf_paths
    for raw_path in state.get("topic_pdf_paths") or []:
        path = Path(raw_path)
        if path.exists():
            # Nếu chỉ có 1 topic thì lấy luôn file đó
            candidates.append(path)

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if path.exists() and resolved not in seen:
            seen.add(resolved)
            unique.append(path)

    if not unique:
        raise FileNotFoundError(f"Topic PDF not found for topic_{pad2(topic_num)}.")

    return unique[0]


def save_topic_metadata_for_job(job_id: str, topic: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    db_name = validate_safe_mongo_db_name(settings.mongo_db_name)
    config = read_json(job_config_path(job_id))
    state = read_json(job_workspace(job_id) / "extraction_state.json")

    topic_num = topic.get("topic_num")
    topic_name = topic.get("topic_name") or topic.get("title") or topic.get("raw_title") or f"Topic {topic_num}"
    class_name = config.get("class_name") or "11"
    subject_name = config.get("subject_name") or "Tin học"
    subject_type = config.get("subject_type") or "Kết nối tri thức"
    book_stem = state.get("book_stem") or config.get("book_name") or Path(config.get("source_pdf_path", "document.pdf")).stem
    bucket = settings.minio_bucket
    prefix = topic_asset_prefix(class_name, subject_name, topic_num)
    object_key = topic_object_key(class_name=class_name, subject_name=subject_name, book_stem=book_stem, topic_num=topic_num)
    source_pdf = _find_topic_pdf(job_id, topic_num)

    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
        db = client[db_name]
        _create_indexes(db, job_id)
        now = _now()

        g_slug = grade_slug(class_name)
        s_slug = subject_slug(subject_name)
        class_id = _upsert_by_import_key(
            db["class"],
            f"class/{g_slug}",
            {"class_name": _class_display_name(class_name)},
            now,
        )
        subject_id = _upsert_by_import_key(
            db.subject,
            f"subject/{g_slug}/{s_slug}",
            {
                "subject_name": subject_name,
                "subject_type": subject_type,
                "bucket_name": bucket,
                "class_id": class_id,
                "asset_prefixes": {"documents": f"documents/{g_slug}/{s_slug}/subject"},
            },
            now,
        )

        import_key = topic_import_key(class_name, subject_name, topic_num)
        topic_id = _upsert_by_import_key(
            db.topic,
            import_key,
            {
                "topic_num": topic_num,
                "topic_name": topic_name,
                "subject_id": subject_id,
                "asset_prefixes": {
                    "documents": prefix,
                    "images": f"images/{g_slug}/{s_slug}/topic/topic_{pad2(topic_num)}",
                    "videos": f"videos/{g_slug}/{s_slug}/topic/topic_{pad2(topic_num)}",
                },
            },
            now,
        )
        _log(job_id, f"mongo_topic_upsert db={db_name} import_key={import_key}")

        _log(job_id, f"minio_topic_upload_start bucket={bucket} object_key={object_key}")
        uploaded = upload_file(source_pdf, bucket, object_key, PDF_CONTENT_TYPE)
        asset_id = _upsert_asset(
            db.asset,
            {
                "owner_type": "topic",
                "owner_id": topic_id,
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
        _log(job_id, f"mongo_topic_asset_upsert object_key={object_key}")

        return {
            "topic_id": str(topic_id),
            "subject_id": str(subject_id),
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
