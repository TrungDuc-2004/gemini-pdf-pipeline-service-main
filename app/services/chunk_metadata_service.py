from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, validate_safe_mongo_db_name
from app.core.logging import append_job_log
from app.core.paths import job_config_path, job_log_path, job_workspace, output_root
from app.services.lesson_metadata_service import lesson_import_key
from app.services.minio_service import upload_file
from app.services.subject_upload_service import grade_slug, safe_file_name, subject_slug
from app.services.sync_service import sync_chunk_to_postgres, sync_chunk_to_neo4j
from app.services.topic_metadata_service import pad2
from app.utils.files import read_json, write_json
from app.utils.time import utc_now_iso

USER_ID = "user_01"
PDF_CONTENT_TYPE = "application/pdf"


def _chunk_id_set(values: list[Any] | None) -> set[str]:
    return {str(value) for value in values or [] if value is not None}


def chunk_asset_prefix(class_name: Any, subject_name: Any, topic_num: Any, lesson_num: Any, chunk_num: Any) -> str:
    return f"documents/{grade_slug(class_name)}/{subject_slug(subject_name)}/chunk/topic_{pad2(topic_num)}-lesson_{pad2(lesson_num)}-chunk_{pad2(chunk_num)}"


def chunk_import_key(class_name: Any, subject_name: Any, lesson_num: Any, chunk_num: Any) -> str:
    return f"chunk/{grade_slug(class_name)}/{subject_slug(subject_name)}/lesson_{pad2(lesson_num)}/chunk_{pad2(chunk_num)}"


def chunk_object_key(*, class_name: Any, subject_name: Any, book_stem: Any, topic_num: Any, lesson_num: Any, chunk_num: Any) -> str:
    prefix = chunk_asset_prefix(class_name, subject_name, topic_num, lesson_num, chunk_num)
    return f"{prefix}/{safe_file_name(book_stem)}_lesson_{pad2(lesson_num)}_chunk_{pad2(chunk_num)}.pdf"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_update(now: datetime) -> dict[str, Any]:
    return {"is_deleted": False, "deleted_at": None, "updated_at": now, "updated_by": USER_ID}


def _audit_insert(now: datetime) -> dict[str, Any]:
    return {"created_at": now, "created_by": USER_ID}


def _log(job_id: str, message: str) -> None:
    append_job_log(job_log_path(job_id), f"{utc_now_iso()} [chunk_metadata] {message}")


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
    for collection, key in [("chunk", "import_key"), ("asset", "object_key")]:
        try:
            db[collection].create_index(key, unique=True)
        except Exception as exc:
            _log(job_id, f"index_warning collection={collection} error={exc}")


def _chunk_num(value: Any) -> str:
    match = re.search(r"\d+", str(value or ""))
    return match.group(0) if match else "1"


def _final_bundle(job_id: str) -> tuple[str, Path]:
    state = read_json(job_workspace(job_id) / "extraction_state.json")
    book_stem = state.get("book_stem")
    if not book_stem:
        raise FileNotFoundError("book_stem missing in extraction_state.json.")
    output_bundle = Path(state.get("final_bundle_path") or output_root() / book_stem)
    if not output_bundle.exists():
        raise FileNotFoundError(f"Final output bundle not found: {output_bundle}")
    return book_stem, output_bundle


def _kaggle_completed(job_id: str) -> bool:
    path = job_workspace(job_id) / "kaggle_result.json"
    if not path.exists():
        return False
    data = read_json(path)
    return bool(data.get("applied")) and data.get("status") == "completed"


def _find_final_chunk_pdf(output_bundle: Path, chunk: dict[str, Any]) -> Path:
    pdf_path = chunk.get("pdf_path") or chunk.get("chunk_pdf")
    if pdf_path:
        original = Path(str(pdf_path))
        if original.exists() and str(original).startswith(str(output_bundle)):
            return original
        parts = list(original.parts)
        if "Chunk" in parts:
            relative = Path(*parts[parts.index("Chunk") :])
            candidate = output_bundle / relative
            if candidate.exists():
                return candidate
    lesson_stem = str(chunk.get("lesson_stem") or "")
    chunk_name = str(chunk.get("chunk") or f"chunk_{pad2(chunk.get('chunk_num'))}")
    folder = output_bundle / "Chunk" / lesson_stem / chunk_name
    candidates = sorted(folder.glob("*.pdf")) if folder.exists() else []
    if not candidates:
        candidates = sorted((output_bundle / "Chunk").glob(f"**/*{chunk_name}*.pdf"))
    if not candidates:
        raise FileNotFoundError(f"Final chunk PDF not found for {lesson_stem}/{chunk_name}.")
    return candidates[0]



def _find_approved_chunk_pdf(job_id: str, chunk: dict[str, Any]) -> Path:
    """Find the current chunk PDF before Kaggle/final bundle.

    Approve-to-sync must not wait for Kaggle. The best source is the PDF path
    generated by chunk extraction/recut; fall back to workspace chunk folders.
    """
    checked: list[str] = []
    for key in ["pdf_path", "chunk_pdf", "final_pdf_path"]:
        raw = chunk.get(key)
        if not raw:
            continue
        candidate = Path(str(raw))
        checked.append(str(candidate))
        if candidate.exists() and candidate.is_file():
            return candidate

    lesson_stem = str(chunk.get("lesson_stem") or "")
    chunk_name = str(chunk.get("chunk") or f"chunk_{pad2(chunk.get('chunk_num'))}")
    roots = [
        job_workspace(job_id) / "chunks" / lesson_stem / chunk_name,
        job_workspace(job_id) / "chunks" / lesson_stem,
        job_workspace(job_id) / "chunks",
    ]
    for root in roots:
        checked.append(str(root))
        if not root.exists():
            continue
        candidates = sorted(root.glob("*.pdf")) if root.is_dir() else []
        if not candidates and root.is_dir() and chunk_name:
            candidates = sorted(root.glob(f"**/*{chunk_name}*.pdf"))
        if candidates:
            return candidates[0]

    raise FileNotFoundError(f"Chunk PDF not found before sync for {lesson_stem}/{chunk_name}. Checked: {checked[:10]}")

def save_final_chunks_after_kaggle(job_id: str, *, force_without_kaggle: bool = False) -> dict[str, Any]:
    if not force_without_kaggle and not _kaggle_completed(job_id):
        raise RuntimeError("Kaggle has not completed. Use force_without_kaggle=true only for debugging.")
    approved_path = job_workspace(job_id) / "approved_chunks.json"
    if not approved_path.exists():
        raise FileNotFoundError("approved_chunks.json not found.")
    approved = read_json(approved_path)
    chunks = [dict(chunk) for chunk in approved.get("chunks", []) if isinstance(chunk, dict) and chunk.get("approved")]
    if not chunks:
        raise ValueError("No approved chunks found.")

    settings = get_settings()
    db_name = validate_safe_mongo_db_name(settings.mongo_db_name)
    config = read_json(job_config_path(job_id))
    class_name = config.get("class_name") or "11"
    subject_name = config.get("subject_name") or "Tin học"
    book_stem, output_bundle = _final_bundle(job_id)
    bucket = settings.minio_bucket

    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    counts = {"chunk_count": 0, "chunk_asset_count": 0, "uploaded_minio_files": 0}
    try:
        client.admin.command("ping")
        db = client[db_name]
        _create_indexes(db, job_id)
        now = _now()
        g_slug = grade_slug(class_name)
        s_slug = subject_slug(subject_name)
        for chunk in chunks:
            lesson_num = chunk.get("lesson_num")
            topic_num = chunk.get("topic_num") or ""
            if not topic_num:
                lesson = db.lesson.find_one({"import_key": lesson_import_key(class_name, subject_name, lesson_num)}, {"_id": 1})
            else:
                lesson = db.lesson.find_one({"import_key": lesson_import_key(class_name, subject_name, lesson_num)}, {"_id": 1})
            if not lesson:
                raise FileNotFoundError(f"Parent lesson not found in MongoDB for lesson_{pad2(lesson_num)}.")
            cnum = _chunk_num(chunk.get("chunk_num") or chunk.get("chunk"))
            prefix = chunk_asset_prefix(class_name, subject_name, topic_num or "00", lesson_num, cnum)
            import_key = chunk_import_key(class_name, subject_name, lesson_num, cnum)
            chunk_id = _upsert_by_import_key(
                db.chunk,
                import_key,
                {
                    "chunk_num": cnum,
                    "chunk_name": chunk.get("chunk_name") or chunk.get("title") or f"Chunk {cnum}",
                    "chunk_category": "document",
                    "chunk_type": chunk.get("chunk_type"),
                    "lesson_id": lesson["_id"],
                    "asset_prefixes": {
                        "documents": prefix,
                        "images": f"images/{g_slug}/{s_slug}/chunk/topic_{pad2(topic_num)}-lesson_{pad2(lesson_num)}-chunk_{pad2(cnum)}",
                        "videos": f"videos/{g_slug}/{s_slug}/chunk/topic_{pad2(topic_num)}-lesson_{pad2(lesson_num)}-chunk_{pad2(cnum)}",
                    },
                },
                now,
            )
            chunk["_job_id"] = job_id
            pdf = _find_final_chunk_pdf(output_bundle, chunk)
            object_key = chunk_object_key(class_name=class_name, subject_name=subject_name, book_stem=book_stem, topic_num=topic_num or "00", lesson_num=lesson_num, chunk_num=cnum)
            uploaded = upload_file(pdf, bucket, object_key, PDF_CONTENT_TYPE)
            asset_id = _upsert_asset(
                db.asset,
                {
                    "owner_type": "chunk",
                    "owner_id": chunk_id,
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
            chunk.update(
                {
                    "metadata_edu_saved": True,
                    "minio_uploaded": True,
                    "kaggle_finalized": True,
                    "waiting_for_kaggle": False,
                    "asset_object_key": object_key,
                    "asset_url": uploaded["url"],
                    "chunk_mongo_id": str(chunk_id),
                    "asset_id": str(asset_id),
                }
            )
            counts["chunk_count"] += 1
            counts["chunk_asset_count"] += 1
            counts["uploaded_minio_files"] += 1
        approved["chunks"] = chunks + [chunk for chunk in approved.get("chunks", []) if not chunk.get("approved")]
        approved["chunks_finalized_after_kaggle"] = counts["chunk_count"]
        approved["updated_at"] = utc_now_iso()
        write_json(approved_path, approved)
        result = {"ok": True, "job_id": job_id, "db_name": db_name, "bucket": bucket, "counts": counts}
        write_json(job_workspace(job_id) / "chunk_finalize_result.json", result)
        _log(job_id, f"finalized chunks count={counts['chunk_count']} db={db_name} bucket={bucket}")
        return result
    finally:
        client.close()


def save_chunks_metadata_and_sync(job_id: str, chunk_ids: list[Any] | None = None) -> dict[str, Any]:
    """
    Khi chunks được duyệt: Lưu metadata vào MongoDB + Sync PostgreSQL + Neo4j ngay.
    Không cần chờ Kaggle.
    """
    approved_path = job_workspace(job_id) / "approved_chunks.json"
    if not approved_path.exists():
        raise FileNotFoundError("approved_chunks.json not found. Approve chunks first.")

    approved = read_json(approved_path)
    chunks = [dict(chunk) for chunk in approved.get("chunks", []) if isinstance(chunk, dict)]
    if not chunks:
        raise ValueError("No chunks found in approved_chunks.json.")

    selected_ids = _chunk_id_set(chunk_ids) if chunk_ids is not None else {
        str(chunk.get("chunk_id") or chunk.get("id"))
        for chunk in chunks
        if chunk.get("approved")
    }
    if not selected_ids:
        raise ValueError("No selected chunk_ids to sync.")

    target_chunks = [chunk for chunk in chunks if str(chunk.get("chunk_id") or chunk.get("id")) in selected_ids]
    if not target_chunks:
        raise ValueError("Selected chunk_ids were not found in approved chunks.")

    settings = get_settings()
    db_name = validate_safe_mongo_db_name(settings.mongo_db_name)
    config = read_json(job_config_path(job_id))
    class_name = config.get("class_name") or "11"
    subject_name = config.get("subject_name") or "Tin học"

    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    bucket = settings.minio_bucket
    counts = {
        "chunk_count": 0,
        "chunk_asset_count": 0,
        "uploaded_minio_files": 0,
        "postgres_synced_count": 0,
        "neo4j_synced_count": 0,
    }
    try:
        client.admin.command("ping")
        db = client[db_name]
        _create_indexes(db, job_id)
        now = _now()

        for chunk in target_chunks:
            if not chunk.get("approved"):
                raise ValueError("Cannot sync chunk that is not approved.")

            lesson_num = chunk.get("lesson_num")
            topic_num = chunk.get("topic_num") or ""

            lesson = db.lesson.find_one({"import_key": lesson_import_key(class_name, subject_name, lesson_num)}, {"_id": 1})
            if not lesson:
                raise FileNotFoundError(f"Parent lesson not found in MongoDB for lesson_{pad2(lesson_num)}.")

            cnum = _chunk_num(chunk.get("chunk_num") or chunk.get("chunk"))
            prefix = chunk_asset_prefix(class_name, subject_name, topic_num or "00", lesson_num, cnum)
            import_key = chunk_import_key(class_name, subject_name, lesson_num, cnum)

            chunk_mongo_id = _upsert_by_import_key(
                db.chunk,
                import_key,
                {
                    "chunk_num": cnum,
                    "chunk_name": chunk.get("chunk_name") or chunk.get("title") or f"Chunk {cnum}",
                    "chunk_category": "document",
                    "chunk_type": chunk.get("chunk_type"),
                    "lesson_id": lesson["_id"],
                    "asset_prefixes": {
                        "documents": prefix,
                        "images": f"images/{grade_slug(class_name)}/{subject_slug(subject_name)}/chunk/topic_{pad2(topic_num)}-lesson_{pad2(lesson_num)}-chunk_{pad2(cnum)}",
                        "videos": f"videos/{grade_slug(class_name)}/{subject_slug(subject_name)}/chunk/topic_{pad2(topic_num)}-lesson_{pad2(lesson_num)}-chunk_{pad2(cnum)}",
                    },
                },
                now,
            )

            source_pdf = _find_approved_chunk_pdf(job_id, chunk)
            state = read_json(job_workspace(job_id) / "extraction_state.json") if (job_workspace(job_id) / "extraction_state.json").exists() else {}
            book_stem = state.get("book_stem") or config.get("book_name") or Path(config.get("source_pdf_path", "document.pdf")).stem
            object_key = chunk_object_key(
                class_name=class_name,
                subject_name=subject_name,
                book_stem=book_stem,
                topic_num=topic_num or "00",
                lesson_num=lesson_num,
                chunk_num=cnum,
            )
            uploaded = upload_file(source_pdf, bucket, object_key, PDF_CONTENT_TYPE)
            asset_id = _upsert_asset(
                db.asset,
                {
                    "owner_type": "chunk",
                    "owner_id": chunk_mongo_id,
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

            chunk.update(
                {
                    "metadata_edu_saved": True,
                    "minio_uploaded": True,
                    "waiting_for_kaggle": False,
                    "asset_object_key": object_key,
                    "asset_url": uploaded["url"],
                    "chunk_mongo_id": str(chunk_mongo_id),
                    "asset_id": str(asset_id),
                    "postgres_synced": False,
                    "neo4j_synced": False,
                }
            )
            counts["chunk_count"] += 1
            counts["chunk_asset_count"] += 1
            counts["uploaded_minio_files"] += 1

        approved["chunks"] = chunks
        approved["chunks_metadata_saved_at"] = utc_now_iso()
        write_json(approved_path, approved)
        _log(job_id, f"chunks metadata saved count={counts['chunk_count']} db={db_name}")

    finally:
        client.close()

    _log(job_id, f"Starting selective sync to PostgreSQL + Neo4j for {counts['chunk_count']} chunks")
    for chunk in target_chunks:
        lesson_num = chunk.get("lesson_num")
        cnum = _chunk_num(chunk.get("chunk_num") or chunk.get("chunk"))
        import_key = chunk_import_key(class_name, subject_name, lesson_num, cnum)

        pg_result = sync_chunk_to_postgres(import_key=import_key, create_schema=True)
        chunk["postgres_synced"] = True
        chunk["postgres_chunk_id"] = pg_result.get("chunk_id")
        counts["postgres_synced_count"] += 1

        neo_result = sync_chunk_to_neo4j(chunk_id=pg_result.get("chunk_id"), create_schema=True, rebuild_neo4j=False)
        chunk["neo4j_synced"] = True
        counts["neo4j_synced_count"] += 1
        _log(job_id, f"chunk synced chunk_id={pg_result.get('chunk_id')} postgres_ok={pg_result.get('ok')} neo_ok={neo_result.get('ok')}")

    approved["chunks"] = chunks
    approved["chunks_synced_at"] = utc_now_iso()
    write_json(approved_path, approved)

    return {
        "ok": True,
        "job_id": job_id,
        "db_name": db_name,
        "counts": counts,
        "message": f"Saved and synced {counts['chunk_count']} chunks to MongoDB/PostgreSQL/Neo4j",
    }
