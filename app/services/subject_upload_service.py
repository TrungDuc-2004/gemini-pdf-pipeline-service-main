from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, validate_safe_mongo_db_name
from app.core.logging import append_job_log
from app.core.paths import job_log_path, job_workspace
from app.services.minio_service import upload_file
from app.utils.time import utc_now_iso


USER_ID = "user_01"
PDF_CONTENT_TYPE = "application/pdf"


def _minio_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "minio_upload.log"


def _log(job_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}"
    append_job_log(_minio_log_path(job_id), line)
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [subject_upload] {message}")


def slugify_vietnamese(text: Any) -> str:
    value = str(text or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def _num(value: Any, fallback: str = "") -> str:
    match = re.search(r"\d+", str(value or ""))
    return match.group(0) if match else fallback


def grade_slug(class_name: Any) -> str:
    grade = _num(class_name, str(class_name or "").strip())
    return f"lop-{slugify_vietnamese(grade)}"


def subject_slug(subject_name: Any) -> str:
    return slugify_vietnamese(subject_name or "Tin học")


def safe_file_name(name: Any) -> str:
    slug = slugify_vietnamese(Path(str(name or "document")).stem)
    return slug or "document"


def _class_display_name(class_name: Any) -> str:
    text = str(class_name or "").strip()
    grade = _num(text)
    if grade and not text.lower().startswith("lớp"):
        return f"Lớp {grade}"
    return text or "Lớp"


def build_subject_asset_prefix(class_name: Any, subject_name: Any) -> str:
    return f"documents/{grade_slug(class_name)}/{subject_slug(subject_name)}/subject"


def build_subject_object_key(*, class_name: Any, subject_name: Any, book_name: Any, job_id: str) -> str:
    prefix = build_subject_asset_prefix(class_name, subject_name)
    return f"{prefix}/{safe_file_name(book_name)}_{job_id[:8]}.pdf"


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
    for collection, key in [("class", "import_key"), ("subject", "import_key"), ("asset", "object_key")]:
        try:
            db[collection].create_index(key, unique=True)
        except Exception as exc:
            _log(job_id, f"index_warning collection={collection} error={exc}")


def upload_subject_pdf_for_job(
    *,
    job_id: str,
    source_pdf_path: str | Path,
    book_name: str,
    class_name: str,
    subject_name: str,
    subject_type: str | None,
) -> dict[str, Any]:
    settings = get_settings()
    bucket = settings.minio_bucket
    prefix = build_subject_asset_prefix(class_name, subject_name)
    object_key = build_subject_object_key(
        class_name=class_name,
        subject_name=subject_name,
        book_name=book_name,
        job_id=job_id,
    )
    source_pdf = Path(source_pdf_path)
    if not source_pdf.exists():
        raise FileNotFoundError(f"Source PDF not found: {source_pdf}")

    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
        db = client[validate_safe_mongo_db_name(settings.mongo_db_name)]
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
        _log(job_id, f"mongo_class_upsert import_key=class/{g_slug}")

        subject_id = _upsert_by_import_key(
            db.subject,
            f"subject/{g_slug}/{s_slug}",
            {
                "subject_name": subject_name or "Tin học",
                "subject_type": subject_type or "Kết nối tri thức",
                "bucket_name": bucket,
                "class_id": class_id,
                "asset_prefixes": {"documents": prefix},
            },
            now,
        )
        _log(job_id, f"mongo_subject_upsert import_key=subject/{g_slug}/{s_slug}")

        _log(job_id, f"minio_upload_start bucket={bucket} object_key={object_key}")
        uploaded = upload_file(source_pdf, bucket, object_key, PDF_CONTENT_TYPE)
        asset_id = _upsert_asset(
            db.asset,
            {
                "owner_type": "subject",
                "owner_id": subject_id,
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
        _log(job_id, f"mongo_asset_upsert object_key={object_key}")
        _log(job_id, f"minio_upload_done bucket={bucket} object_key={object_key} size={uploaded['size']}")

        return {
            "enabled": True,
            "bucket": bucket,
            "subject_asset_uploaded": True,
            "subject_prefix": prefix,
            "subject_object_key": object_key,
            "subject_url": uploaded["url"],
            "subject_id": str(subject_id),
            "asset_id": str(asset_id),
            "uploaded_at": utc_now_iso(),
        }
    finally:
        client.close()
