from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, validate_safe_mongo_db_name
from app.core.logging import append_job_log
from app.core.paths import job_config_path, job_log_path, job_workspace
from app.services.chunk_metadata_service import chunk_import_key
from app.services.subject_upload_service import slugify_vietnamese
from app.services.topic_metadata_service import pad2, topic_import_key
from app.utils.files import read_json, write_json
from app.utils.time import utc_now_iso

USER_ID = "user_01"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_update(now: datetime) -> dict[str, Any]:
    return {"is_deleted": False, "deleted_at": None, "updated_at": now, "updated_by": USER_ID}


def _audit_insert(now: datetime) -> dict[str, Any]:
    return {"created_at": now, "created_by": USER_ID}


def _log(job_id: str, message: str) -> None:
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [keyword_metadata] {message}")


def _create_indexes(db: Any, job_id: str) -> None:
    specs = [
        ("keyword", "keyword_slug"),
        ("chunk_keyword", "import_key"),
        ("topic_bag", "import_key"),
    ]
    for collection, key in specs:
        try:
            db[collection].create_index(key, unique=True)
        except Exception as exc:
            _log(job_id, f"index_warning collection={collection} error={exc}")


def _upsert_keyword(db: Any, keyword_name: str, now: datetime) -> Any:
    slug = slugify_vietnamese(keyword_name)
    db.keyword.update_one(
        {"keyword_slug": slug},
        {
            "$set": {
                "keyword_name": keyword_name,
                "keyword_slug": slug,
                "aliases": [],
                "asset_prefixes": {
                    "images": f"images/keyword/{slug}",
                    "videos": f"videos/keyword/{slug}",
                },
                **_audit_update(now),
            },
            "$setOnInsert": _audit_insert(now),
        },
        upsert=True,
    )
    return db.keyword.find_one({"keyword_slug": slug}, {"_id": 1})["_id"], slug


def _chunk_meta_from_keyword_path(keyword_path: Path) -> dict[str, Any] | None:
    meta_path = keyword_path.with_suffix("").with_suffix(".json")
    if not meta_path.exists():
        return None
    data = read_json(meta_path)
    return data if isinstance(data, dict) else None


def _keywords_from_file(path: Path) -> list[str]:
    try:
        data = read_json(path)
    except Exception:
        return []
    if data.get("error"):
        return []
    out = []
    for item in data.get("keywords") or []:
        value = item.get("keyword") if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out


def save_keyword_metadata_for_job(job_id: str, *, output_bundle: Path) -> dict[str, Any]:
    settings = get_settings()
    db_name = validate_safe_mongo_db_name(settings.mongo_db_name)
    config = read_json(job_config_path(job_id))
    class_name = config.get("class_name") or "11"
    subject_name = config.get("subject_name") or "Tin học"

    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    counts = {
        "keyword_count": 0,
        "keyword_alias_count": 0,
        "chunk_keyword_count": 0,
        "topic_bag_count": 0,
        "skipped_keyword_files": 0,
    }
    topic_keywords: dict[str, dict[str, Any]] = {}
    try:
        client.admin.command("ping")
        db = client[db_name]
        _create_indexes(db, job_id)
        now = _now()
        for keyword_path in sorted((output_bundle / "Chunk").rglob("*.keywords.json")):
            meta = _chunk_meta_from_keyword_path(keyword_path)
            keywords = _keywords_from_file(keyword_path)
            if not meta or not keywords:
                counts["skipped_keyword_files"] += 1
                continue
            lesson_num = meta.get("lesson_num")
            chunk_num = meta.get("chunk_num") or meta.get("chunk")
            topic_num = meta.get("topic_num") or "00"
            chunk_key = chunk_import_key(class_name, subject_name, lesson_num, chunk_num)
            chunk_doc = db.chunk.find_one({"import_key": chunk_key}, {"_id": 1})
            if not chunk_doc:
                counts["skipped_keyword_files"] += 1
                continue
            topic_key = topic_import_key(class_name, subject_name, topic_num)
            topic_doc = db.topic.find_one({"import_key": topic_key}, {"_id": 1, "topic_name": 1})
            for keyword_name in keywords:
                keyword_id, slug = _upsert_keyword(db, keyword_name, now)
                counts["keyword_count"] += 1
                ck_key = f"chunk_keyword/{chunk_key}/{slug}"
                db.chunk_keyword.update_one(
                    {"import_key": ck_key},
                    {
                        "$set": {
                            "import_key": ck_key,
                            "chunk_id": chunk_doc["_id"],
                            "keyword_id": keyword_id,
                            **_audit_update(now),
                        },
                        "$setOnInsert": _audit_insert(now),
                    },
                    upsert=True,
                )
                counts["chunk_keyword_count"] += 1
                if topic_doc:
                    bag = topic_keywords.setdefault(
                        str(topic_doc["_id"]),
                        {"topic_id": topic_doc["_id"], "topic_name": topic_doc.get("topic_name") or meta.get("topic_name") or "", "refs": {}},
                    )
                    bag["refs"][str(keyword_id)] = {"keyword_id": keyword_id, "keyword_name": keyword_name}
        for topic_id_str, bag in topic_keywords.items():
            refs = list(bag["refs"].values())
            import_key = f"topic_bag/{topic_id_str}"
            names = [ref["keyword_name"].lower() for ref in refs]
            db.topic_bag.update_one(
                {"import_key": import_key},
                {
                    "$set": {
                        "import_key": import_key,
                        "topic_id": bag["topic_id"],
                        "topic_name": bag["topic_name"],
                        "keyword_refs": refs,
                        "total_keywords": len(refs),
                        "keyword_embedding_text": " | ".join(names),
                        **_audit_update(now),
                    },
                    "$setOnInsert": _audit_insert(now),
                },
                upsert=True,
            )
            counts["topic_bag_count"] += 1
        result = {"ok": True, "job_id": job_id, "db_name": db_name, "counts": counts}
        write_json(job_workspace(job_id) / "keyword_metadata_result.json", result)
        _log(job_id, f"saved keyword metadata counts={counts}")
        return result
    finally:
        client.close()
