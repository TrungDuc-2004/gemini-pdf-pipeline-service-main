from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, validate_safe_mongo_db_name
from app.core.logging import append_job_log
from app.core.paths import job_config_path, job_log_path, job_source_pdf_path, job_workspace
from app.models.job_models import JobStatus
from app.services.bundle_service import build_bundle_summary
from app.services.job_service import ensure_job_exists, update_job_state
from app.services.minio_service import upload_file
from app.services.progress_service import update_progress, update_result
from app.utils.files import read_json, write_json
from app.utils.time import utc_now_iso


USER_ID = "user_01"
PDF_CONTENT_TYPE = "application/pdf"


def _workspace_file(job_id: str, name: str) -> Path:
    return job_workspace(job_id) / name


def _mongo_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "mongo_import.log"


def _minio_log_path(job_id: str) -> Path:
    return job_workspace(job_id) / "logs" / "minio_upload.log"


def _log(job_id: str, message: str) -> None:
    line = f"{utc_now_iso()} {message}"
    append_job_log(_mongo_log_path(job_id), line)
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [metadata_edu_import] {message}")


def _log_minio(job_id: str, message: str) -> None:
    append_job_log(_minio_log_path(job_id), f"{utc_now_iso()} {message}")


def slugify_vietnamese(text: Any) -> str:
    value = str(text or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def _num(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    match = re.search(r"\d+", str(value))
    return match.group(0) if match else (str(value).strip() or fallback)


def _int_num(value: Any, fallback: int = 0) -> int:
    text = _num(value, "")
    return int(text) if text.isdigit() else fallback


def pad2(value: Any) -> str:
    return f"{_int_num(value):02d}"


def grade_slug(class_name: Any) -> str:
    grade = _num(class_name, str(class_name or "").strip())
    return f"lop-{slugify_vietnamese(grade)}"


def subject_slug(subject_name: Any) -> str:
    return slugify_vietnamese(subject_name or "Tin học")


def safe_file_name(name: Any) -> str:
    stem = slugify_vietnamese(Path(str(name or "document")).stem)
    return stem or "document"


def _class_display_name(class_name: Any) -> str:
    text = str(class_name or "").strip()
    grade = _num(text, "")
    if grade and not text.lower().startswith("lớp"):
        return f"Lớp {grade}"
    return text or "Lớp"


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


def _load_json_optional(path: Path) -> dict[str, Any] | None:
    try:
        data = read_json(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _find_pdf_sibling(json_path: Path) -> Path | None:
    pdfs = sorted(json_path.parent.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def _json_files(root: Path, kind: str) -> list[Path]:
    if not root.exists():
        return []
    paths: list[Path] = []
    for path in sorted(root.rglob("*.json")):
        if path.name.endswith(".keywords.json"):
            continue
        if "DebugCutlines" in path.parts:
            continue
        if kind == "chunk" and not (path.parent.name.startswith("chunk_") and "_chunk_" in path.stem):
            continue
        if kind in {"topic", "lesson"} and path.name in {"topic_meta.json", "lesson_meta.json"}:
            continue
        paths.append(path)
    return paths


def _read_bundle(job_id: str) -> tuple[str, Path, dict[str, Any], dict[str, Any]]:
    state = read_json(_workspace_file(job_id, "job_state.json"))
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
    return book_stem, bundle_path, read_json(manifest_path), state


def _topic_docs(bundle_path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_by_num = {}
    for item in _load_manifest_items(manifest, "list_topic"):
        topic_num = str(item.get("topic_num") or _num(item.get("name"), ""))
        if topic_num:
            manifest_by_num[topic_num] = item

    docs = []
    for json_path in _json_files(bundle_path / "Topic", "topic"):
        meta = _load_json_optional(json_path)
        if not meta:
            continue
        topic_num = str(meta.get("topic_num") or _num(meta.get("name") or json_path.parent.name, ""))
        merged = {**manifest_by_num.get(topic_num, {}), **meta}
        docs.append(
            {
                "topic_num": _int_num(topic_num),
                "topic_name": merged.get("topic_name") or merged.get("title") or merged.get("raw_title") or f"Chủ đề {topic_num}",
                "pdf_path": str(merged.get("pdf") or merged.get("pdf_path") or _find_pdf_sibling(json_path) or ""),
                "raw": merged,
            }
        )
    docs.sort(key=lambda item: item["topic_num"])
    return docs


def _lesson_docs(bundle_path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_by_num = {}
    for item in _load_manifest_items(manifest, "list_lesson"):
        lesson_num = str(item.get("lesson_num") or _num(item.get("name"), ""))
        if lesson_num:
            manifest_by_num[lesson_num] = item

    docs = []
    for json_path in _json_files(bundle_path / "Lesson", "lesson"):
        meta = _load_json_optional(json_path)
        if not meta:
            continue
        lesson_num = str(meta.get("lesson_num") or _num(meta.get("name") or json_path.parent.name, ""))
        merged = {**manifest_by_num.get(lesson_num, {}), **meta}
        lesson_name = merged.get("lesson_name") or merged.get("title") or merged.get("raw_title") or f"Bài {lesson_num}"
        docs.append(
            {
                "lesson_num": _int_num(lesson_num),
                "lesson_name": lesson_name,
                "topic_num": _int_num(merged.get("topic_num")),
                "topic_name": merged.get("topic_name"),
                "lesson_type": _infer_lesson_type(lesson_name, merged.get("lesson_type")),
                "pdf_path": str(merged.get("pdf") or merged.get("pdf_path") or _find_pdf_sibling(json_path) or ""),
                "raw": merged,
            }
        )
    docs.sort(key=lambda item: item["lesson_num"])
    return docs


def _chunk_docs(bundle_path: Path, lesson_by_num: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    docs = []
    for json_path in _json_files(bundle_path / "Chunk", "chunk"):
        meta = _load_json_optional(json_path)
        if not meta:
            continue
        lesson_num = _int_num(meta.get("lesson_num") or meta.get("lesson_stem") or json_path.parent.parent.name)
        lesson = lesson_by_num.get(lesson_num, {})
        chunk_num = _int_num(meta.get("chunk_num") or meta.get("chunk") or json_path.parent.name)
        chunk_name = meta.get("chunk_name") or meta.get("title") or f"Chunk {chunk_num}"
        docs.append(
            {
                "chunk_num": chunk_num,
                "chunk_name": chunk_name,
                "lesson_num": lesson_num,
                "lesson_name": meta.get("lesson_name") or lesson.get("lesson_name"),
                "topic_num": _int_num(meta.get("topic_num") or lesson.get("topic_num")),
                "pdf_path": str(meta.get("pdf_path") or meta.get("chunk_pdf") or _find_pdf_sibling(json_path) or ""),
                "keyword_path": str(json_path.with_suffix(".keywords.json")),
                "raw": meta,
            }
        )
    docs.sort(key=lambda item: (item["lesson_num"], item["chunk_num"]))
    return docs


def _infer_lesson_type(lesson_name: Any, existing: Any = None) -> str:
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    normalized = slugify_vietnamese(lesson_name)
    return "thuc hanh" if "thuc-hanh" in normalized else "ly thuyet"


def _asset_prefixes(prefix: str) -> dict[str, str]:
    suffix = prefix.split("/", 1)[1] if prefix.startswith("documents/") else prefix
    return {
        "documents": prefix,
        "images": f"images/{suffix}",
        "videos": f"videos/{suffix}",
    }


def _upsert_import_key(collection: Any, import_key: str, doc: dict[str, Any], now: datetime) -> Any:
    payload = {**doc, "import_key": import_key, **_audit_update(now)}
    collection.update_one(
        {"import_key": import_key},
        {"$set": payload, "$setOnInsert": _audit_insert(now)},
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


def _upsert_keyword(collection: Any, keyword_name: str, now: datetime) -> Any:
    slug = slugify_vietnamese(keyword_name)
    suffix = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:6]
    doc = {
        "keyword_name": keyword_name,
        "keyword_slug": slug,
        "aliases": [],
        "asset_prefixes": {
            "images": f"images/keyword/{slug}__{suffix}",
            "videos": f"videos/keyword/{slug}__{suffix}",
        },
        "import_key": f"keyword/{slug}",
        **_audit_update(now),
    }
    collection.update_one(
        {"keyword_slug": slug},
        {"$set": doc, "$setOnInsert": _audit_insert(now)},
        upsert=True,
    )
    found = collection.find_one({"keyword_slug": slug}, {"_id": 1})
    return found["_id"]


def _upsert_chunk_keyword(collection: Any, chunk_id: Any, keyword_id: Any, now: datetime) -> None:
    collection.update_one(
        {"chunk_id": chunk_id, "keyword_id": keyword_id},
        {
            "$set": {"chunk_id": chunk_id, "keyword_id": keyword_id, **_audit_update(now)},
            "$setOnInsert": _audit_insert(now),
        },
        upsert=True,
    )


def _upsert_keyword_alias(collection: Any, keyword_id: Any, keyword_name: str, alias_name: str) -> None:
    alias_norm = slugify_vietnamese(alias_name).replace("-", " ")
    collection.update_one(
        {"keyword_id": keyword_id, "alias_norm": alias_norm},
        {
            "$set": {
                "keyword_id": keyword_id,
                "keyword_name": keyword_name,
                "alias_name": alias_name,
                "alias_norm": alias_norm,
            }
        },
        upsert=True,
    )


def _extract_keyword_items(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or data.get("error"):
        return []
    raw = data.get("keywords")
    if raw is None and isinstance(data.get("data"), dict):
        raw = data["data"].get("keywords")
    if not isinstance(raw, list):
        return []

    items = []
    seen = set()
    for item in raw:
        aliases: list[str] = []
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("keyword") or item.get("keyword_name") or item.get("name") or item.get("text")
            raw_aliases = item.get("aliases") or item.get("alias") or []
            aliases = [raw_aliases] if isinstance(raw_aliases, str) else list(raw_aliases or [])
        else:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        slug = slugify_vietnamese(name)
        if slug in seen:
            continue
        seen.add(slug)
        items.append({"keyword_name": name.strip(), "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()]})
    return items


def _create_indexes(db: Any, job_id: str) -> None:
    index_specs = [
        ("class", [("import_key", 1)], True),
        ("subject", [("import_key", 1)], True),
        ("topic", [("import_key", 1)], True),
        ("lesson", [("import_key", 1)], True),
        ("chunk", [("import_key", 1)], True),
        ("asset", [("object_key", 1)], True),
        ("keyword", [("keyword_slug", 1)], True),
        ("keyword_alias", [("keyword_id", 1), ("alias_norm", 1)], True),
        ("chunk_keyword", [("chunk_id", 1), ("keyword_id", 1)], True),
        ("topic_bag", [("topic_id", 1)], True),
        ("import_job", [("import_key", 1)], True),
    ]
    for collection, keys, unique in index_specs:
        try:
            db[collection].create_index(keys, unique=unique)
        except Exception as exc:
            _log(job_id, f"index_warning collection={collection} error={exc}")


def _upload_asset(
    *,
    job_id: str,
    db: Any,
    owner_type: str,
    owner_id: Any,
    local_path: str,
    path_prefix: str,
    file_name: str,
    bucket: str,
    upload_minio: bool,
    counts: dict[str, int],
    now: datetime,
) -> None:
    path = Path(local_path)
    if not local_path or not path.exists():
        _log_minio(job_id, f"skip_missing owner_type={owner_type} path={local_path}")
        return

    object_key = f"{path_prefix}/{file_name}"
    if not upload_minio:
        _log_minio(job_id, f"skip_upload upload_minio=false object_key={object_key}")
        return

    try:
        uploaded = upload_file(path, bucket, object_key, PDF_CONTENT_TYPE)
        asset_doc = {
            "owner_type": owner_type,
            "owner_id": owner_id,
            "asset_type": "document",
            "bucket": bucket,
            "path_prefix": path_prefix,
            "object_key": object_key,
            "file_name": file_name,
            "url": uploaded["url"],
            "content_type": PDF_CONTENT_TYPE,
            "size": uploaded["size"],
        }
        _upsert_asset(db.asset, asset_doc, now)
        counts["asset_count"] += 1
        counts[f"{owner_type}_asset_count"] += 1
        counts["uploaded_minio_files"] += 1
        _log_minio(job_id, f"uploaded object_key={object_key} size={uploaded['size']}")
    except Exception as exc:
        counts["failed_minio_files"] += 1
        _log_minio(job_id, f"failed object_key={object_key} error={exc}")
        raise


def _counts_template() -> dict[str, int]:
    return {
        "class_count": 0,
        "subject_count": 0,
        "topic_count": 0,
        "lesson_count": 0,
        "chunk_count": 0,
        "asset_count": 0,
        "subject_asset_count": 0,
        "topic_asset_count": 0,
        "lesson_asset_count": 0,
        "chunk_asset_count": 0,
        "keyword_count": 0,
        "keyword_alias_count": 0,
        "chunk_keyword_count": 0,
        "topic_bag_count": 0,
        "skipped_keyword_files": 0,
        "error_keyword_files": 0,
        "uploaded_minio_files": 0,
        "failed_minio_files": 0,
    }


def import_bundle_to_metadata_edu(
    job_id: str,
    *,
    upload_minio: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    ensure_job_exists(job_id)
    started_at = utc_now_iso()
    counts = _counts_template()
    errors: list[dict[str, Any]] = []

    try:
        book_stem, bundle_path, manifest, state = _read_bundle(job_id)
        config = read_json(job_config_path(job_id))
        settings = get_settings()

        class_name = _class_display_name(config.get("class_name") or state.get("class_name") or "11")
        subject_name = str(config.get("subject_name") or state.get("subject_name") or "Tin học")
        subject_type = str(config.get("subject_type") or state.get("subject_type") or "Kết nối tri thức")
        g_slug = grade_slug(class_name)
        s_slug = subject_slug(subject_name)
        safe_stem = safe_file_name(book_stem)
        bucket = settings.minio_bucket

        topics = _topic_docs(bundle_path, manifest)
        lessons = _lesson_docs(bundle_path, manifest)
        lesson_by_num = {lesson["lesson_num"]: lesson for lesson in lessons}
        chunks = _chunk_docs(bundle_path, lesson_by_num)

        counts["class_count"] = 1
        counts["subject_count"] = 1
        counts["topic_count"] = len(topics)
        counts["lesson_count"] = len(lessons)
        counts["chunk_count"] = len(chunks)

        if dry_run:
            for chunk in chunks:
                keyword_path = Path(chunk["keyword_path"])
                data = _load_json_optional(keyword_path)
                if data and data.get("error"):
                    counts["error_keyword_files"] += 1
                if not _extract_keyword_items(data):
                    counts["skipped_keyword_files"] += 1
            result = {
                "ok": True,
                "dry_run": True,
                "job_id": job_id,
                "status": state.get("status"),
                "db_name": settings.mongo_db_name,
                "bucket": bucket,
                "bundle_path": str(bundle_path),
                "counts": counts,
                "errors": errors,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
            }
            write_json(_workspace_file(job_id, "mongo_import_result.json"), result)
            return result

        update_job_state(job_id, status=JobStatus.importing_mongodb, stage="importing_mongodb")
        update_progress(
            job_id,
            status=JobStatus.importing_mongodb,
            stage="importing_mongodb",
            message="Importing Metadata-Edu documents and uploading PDFs to MinIO.",
            percent=0,
            current=0,
            total=max(1, len(topics) + len(lessons) + len(chunks)),
        )

        from pymongo import MongoClient

        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        db_name = validate_safe_mongo_db_name(settings.mongo_db_name)
        db = client[db_name]
        _create_indexes(db, job_id)

        now = _now()
        _log(job_id, f"mongo_db={db_name}")
        _log(job_id, f"bucket={bucket}")
        _log(job_id, f"bundle_path={bundle_path}")

        import_key = f"import_job/{job_id}"
        db.import_job.update_one(
            {"import_key": import_key},
            {
                "$set": {
                    "import_key": import_key,
                    "job_id": job_id,
                    "book_stem": book_stem,
                    "bundle_path": str(bundle_path),
                    "status": "running",
                    "schema": "metadata_edu",
                    "upload_minio": upload_minio,
                    "started_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": _audit_insert(now),
            },
            upsert=True,
        )

        class_id = _upsert_import_key(
            db["class"],
            f"class/{g_slug}",
            {"class_name": class_name},
            now,
        )
        subject_prefix = f"documents/{g_slug}/{s_slug}/subject"
        subject_id = _upsert_import_key(
            db.subject,
            f"subject/{g_slug}/{s_slug}",
            {
                "subject_name": subject_name,
                "subject_category": "document",
                "subject_type": subject_type,
                "bucket_name": bucket,
                "class_id": class_id,
                "asset_prefixes": {"documents": subject_prefix},
            },
            now,
        )

        source_pdf = Path(config.get("source_pdf_path") or state.get("source_pdf_path") or job_source_pdf_path(job_id))
        _upload_asset(
            job_id=job_id,
            db=db,
            owner_type="subject",
            owner_id=subject_id,
            local_path=str(source_pdf),
            path_prefix=subject_prefix,
            file_name=f"{safe_stem}.pdf",
            bucket=bucket,
            upload_minio=upload_minio,
            counts=counts,
            now=now,
        )

        topic_ids: dict[int, Any] = {}
        topic_names: dict[int, str] = {}
        for topic in topics:
            topic_nn = pad2(topic["topic_num"])
            prefix = f"documents/{g_slug}/{s_slug}/topic/topic_{topic_nn}"
            topic_id = _upsert_import_key(
                db.topic,
                f"topic/{g_slug}/{s_slug}/topic_{topic_nn}",
                {
                    "topic_num": topic["topic_num"],
                    "topic_name": topic["topic_name"],
                    "topic_category": "document",
                    "subject_id": subject_id,
                    "asset_prefixes": _asset_prefixes(prefix),
                },
                now,
            )
            topic_ids[topic["topic_num"]] = topic_id
            topic_names[topic["topic_num"]] = topic["topic_name"]
            _upload_asset(
                job_id=job_id,
                db=db,
                owner_type="topic",
                owner_id=topic_id,
                local_path=topic["pdf_path"],
                path_prefix=prefix,
                file_name=f"{safe_stem}_topic_{topic_nn}.pdf",
                bucket=bucket,
                upload_minio=upload_minio,
                counts=counts,
                now=now,
            )

        lesson_ids: dict[int, Any] = {}
        lesson_topic_nums: dict[int, int] = {}
        for lesson in lessons:
            lesson_nn = pad2(lesson["lesson_num"])
            topic_num = lesson["topic_num"] or 0
            topic_nn = pad2(topic_num)
            prefix = f"documents/{g_slug}/{s_slug}/lesson/topic_{topic_nn}-lesson_{lesson_nn}"
            lesson_id = _upsert_import_key(
                db.lesson,
                f"lesson/{g_slug}/{s_slug}/lesson_{lesson_nn}",
                {
                    "lesson_num": lesson["lesson_num"],
                    "lesson_name": lesson["lesson_name"],
                    "lesson_category": "document",
                    "lesson_type": lesson["lesson_type"],
                    "topic_id": topic_ids.get(topic_num),
                    "asset_prefixes": _asset_prefixes(prefix),
                },
                now,
            )
            lesson_ids[lesson["lesson_num"]] = lesson_id
            lesson_topic_nums[lesson["lesson_num"]] = topic_num
            _upload_asset(
                job_id=job_id,
                db=db,
                owner_type="lesson",
                owner_id=lesson_id,
                local_path=lesson["pdf_path"],
                path_prefix=prefix,
                file_name=f"{safe_stem}_lesson_{lesson_nn}.pdf",
                bucket=bucket,
                upload_minio=upload_minio,
                counts=counts,
                now=now,
            )

        chunk_ids: dict[str, Any] = {}
        chunk_topic_nums: dict[str, int] = {}
        for chunk in chunks:
            lesson_num = chunk["lesson_num"]
            lesson_nn = pad2(lesson_num)
            chunk_nn = pad2(chunk["chunk_num"])
            topic_num = chunk["topic_num"] or lesson_topic_nums.get(lesson_num, 0)
            topic_nn = pad2(topic_num)
            prefix = f"documents/{g_slug}/{s_slug}/chunk/topic_{topic_nn}-lesson_{lesson_nn}-chunk_{chunk_nn}"
            chunk_key = f"chunk/{g_slug}/{s_slug}/lesson_{lesson_nn}/chunk_{chunk_nn}"
            chunk_id = _upsert_import_key(
                db.chunk,
                chunk_key,
                {
                    "chunk_num": chunk["chunk_num"],
                    "chunk_name": chunk["chunk_name"],
                    "chunk_category": "document",
                    "chunk_type": chunk.get("chunk_type"),
                    "lesson_id": lesson_ids.get(lesson_num),
                    "asset_prefixes": _asset_prefixes(prefix),
                },
                now,
            )
            chunk_ids[chunk_key] = chunk_id
            chunk_topic_nums[chunk_key] = topic_num
            _upload_asset(
                job_id=job_id,
                db=db,
                owner_type="chunk",
                owner_id=chunk_id,
                local_path=chunk["pdf_path"],
                path_prefix=prefix,
                file_name=f"{safe_stem}_lesson_{lesson_nn}_chunk_{chunk_nn}.pdf",
                bucket=bucket,
                upload_minio=upload_minio,
                counts=counts,
                now=now,
            )

        seen_keywords: set[str] = set()
        seen_relations: set[tuple[str, str]] = set()
        seen_aliases: set[tuple[str, str]] = set()
        topic_keyword_refs: dict[int, dict[str, dict[str, Any]]] = {}

        for chunk in chunks:
            lesson_nn = pad2(chunk["lesson_num"])
            chunk_nn = pad2(chunk["chunk_num"])
            chunk_key = f"chunk/{g_slug}/{s_slug}/lesson_{lesson_nn}/chunk_{chunk_nn}"
            chunk_id = chunk_ids.get(chunk_key)
            topic_num = chunk_topic_nums.get(chunk_key, 0)
            keyword_path = Path(chunk["keyword_path"])
            data = _load_json_optional(keyword_path)
            if data and data.get("error"):
                counts["error_keyword_files"] += 1
            keyword_items = _extract_keyword_items(data)
            if not keyword_items:
                counts["skipped_keyword_files"] += 1
                continue

            for item in keyword_items:
                keyword_name = item["keyword_name"]
                keyword_slug = slugify_vietnamese(keyword_name)
                keyword_id = _upsert_keyword(db.keyword, keyword_name, now)
                if keyword_slug not in seen_keywords:
                    seen_keywords.add(keyword_slug)
                    counts["keyword_count"] += 1

                for alias in item["aliases"]:
                    alias_key = (keyword_slug, slugify_vietnamese(alias))
                    if alias_key in seen_aliases:
                        continue
                    _upsert_keyword_alias(db.keyword_alias, keyword_id, keyword_name, alias)
                    seen_aliases.add(alias_key)
                    counts["keyword_alias_count"] += 1

                relation_key = (str(chunk_id), str(keyword_id))
                if relation_key not in seen_relations:
                    _upsert_chunk_keyword(db.chunk_keyword, chunk_id, keyword_id, now)
                    seen_relations.add(relation_key)
                    counts["chunk_keyword_count"] += 1

                if topic_num:
                    topic_keyword_refs.setdefault(topic_num, {})[keyword_slug] = {
                        "keyword_id": keyword_id,
                        "keyword_name": keyword_name,
                    }

        for topic_num, refs_by_slug in topic_keyword_refs.items():
            refs = sorted(refs_by_slug.values(), key=lambda item: item["keyword_name"].lower())
            keyword_text = " | ".join(item["keyword_name"].lower() for item in refs)
            db.topic_bag.update_one(
                {"topic_id": topic_ids.get(topic_num)},
                {
                    "$set": {
                        "topic_id": topic_ids.get(topic_num),
                        "topic_name": topic_names.get(topic_num, f"Chủ đề {topic_num}"),
                        "keyword_refs": refs,
                        "total_keywords": len(refs),
                        "keyword_embedding_text": keyword_text,
                        **_audit_update(now),
                    },
                    "$setOnInsert": _audit_insert(now),
                },
                upsert=True,
            )
            counts["topic_bag_count"] += 1

        completed_at = utc_now_iso()
        result = {
            "ok": True,
            "job_id": job_id,
            "status": JobStatus.mongodb_imported.value,
            "schema": "metadata_edu",
            "book_stem": book_stem,
            "bundle_path": str(bundle_path),
            "db_name": settings.mongo_db_name,
            "bucket": bucket,
            "upload_minio": upload_minio,
            "counts": counts,
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
                    "completed_at": _now(),
                    "updated_at": _now(),
                }
            },
            upsert=True,
        )
        update_job_state(job_id, status=JobStatus.mongodb_imported, stage="mongodb_imported")
        update_progress(
            job_id,
            status=JobStatus.mongodb_imported,
            stage="mongodb_imported",
            message="Metadata-Edu MongoDB import completed.",
            percent=100,
            current=counts["chunk_count"],
            total=counts["chunk_count"],
        )
        update_result(
            job_id,
            ok=True,
            status=JobStatus.mongodb_imported,
            message="Metadata-Edu MongoDB import completed.",
            data=result,
        )
        _log(job_id, f"success counts={json.dumps(counts, ensure_ascii=False)}")
        _log_minio(job_id, f"summary uploaded={counts['uploaded_minio_files']} failed={counts['failed_minio_files']}")
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
                    "schema": "metadata_edu",
                    "counts": counts,
                    "errors": errors,
                    "started_at": started_at,
                    "completed_at": completed_at,
                },
            )
            update_job_state(job_id, status=JobStatus.error, stage="importing_mongodb", error=error)
            update_progress(job_id, status=JobStatus.error, stage="importing_mongodb", message=error, percent=0)
            update_result(job_id, ok=False, status=JobStatus.error, message="Metadata-Edu MongoDB import failed.", error=error)
            _log(job_id, f"failure error={error}")
            _log_minio(job_id, f"failure error={error}")
        except Exception:
            pass
        raise


def read_mongo_import_result(job_id: str) -> dict[str, Any]:
    ensure_job_exists(job_id)
    path = _workspace_file(job_id, "mongo_import_result.json")
    if not path.exists():
        raise FileNotFoundError("mongo_import_result.json not found.")
    return read_json(path)
