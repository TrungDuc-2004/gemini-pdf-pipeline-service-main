from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings, validate_safe_mongo_db_name
from app.models import postgres_models as pgm
from app.services.e5_embedding_service import MODEL_SHORT, embed_passage, normalize_embedding_text
from app.services.neo4j_service import (
    clear_learning_graph,
    ensure_neo4j_schema,
    neo4j_session,
    upsert_chunk,
    upsert_chunk_keyword,
    upsert_class,
    upsert_lesson,
    upsert_subject,
    upsert_topic,
)
from app.services.postgres_service import ensure_postgres_schema, postgres_session
from app.utils.time import utc_now_iso

CORE_ORDER = ["class", "subject", "topic", "lesson", "chunk", "keyword", "keyword_alias", "chunk_keyword", "topic_bag", "asset", "import_job"]
ACTIVE_FILTER = {"is_deleted": {"$ne": True}}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _object_id_class():
    try:
        from bson import ObjectId
        return ObjectId
    except Exception:
        return None


def _to_object_id(value: Any):
    ObjectId = _object_id_class()
    if ObjectId is None:
        return None
    try:
        text = str(value)
        if re.fullmatch(r"[0-9a-fA-F]{24}", text):
            return ObjectId(text)
    except Exception:
        return None
    return None


def _oid(value: Any) -> str | None:
    if value is None:
        return None
    ObjectId = _object_id_class()
    if ObjectId is not None and isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict) and "$oid" in value:
        return str(value["$oid"])
    return str(value)


def _jsonable(value: Any) -> Any:
    ObjectId = _object_id_class()
    if ObjectId is not None and isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _slugify(text: Any) -> str:
    value = str(text or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"


def _stable_id(prefix: str, doc: dict[str, Any], *parts: Any, max_len: int = 180) -> str:
    seed = doc.get("import_key") or "::".join(str(p) for p in parts if p is not None) or _oid(doc.get("_id")) or prefix
    slug = _slugify(seed)
    value = f"{prefix}_{slug}"
    if len(value) <= max_len:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{slug[: max_len - len(prefix) - 14]}_{digest}"


def _keyword_id(doc: dict[str, Any]) -> str:
    slug = str(doc.get("keyword_slug") or _slugify(doc.get("keyword_name"))).replace("-", "_")
    return f"kw_{_slugify(slug)}"


def _first_doc(docs: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for doc in docs:
        return doc
    return None


def _mongo_doc(db: Any, collection: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    text = str(value)
    object_id = _to_object_id(text)
    if object_id is not None:
        found = db[collection].find_one({"_id": object_id})
        if found:
            return found
    return db[collection].find_one({"_id": value}) or db[collection].find_one({"mongo_id": text})


def _active_docs(db: Any, collection: str) -> list[dict[str, Any]]:
    return list(db[collection].find(ACTIVE_FILTER))


def _upsert_by_pk(session: Session, model: Any, pk_name: str, pk_value: str, values: dict[str, Any]) -> Any:
    obj = session.get(model, pk_value)
    if obj is None:
        obj = model(**{pk_name: pk_value})
        session.add(obj)
    for key, value in values.items():
        if hasattr(obj, key):
            setattr(obj, key, value)
    if hasattr(obj, "synced_at"):
        setattr(obj, "synced_at", _now())
    session.flush()
    return obj


def _get_pg_id_by_mongo(session: Session, model: Any, id_attr: str, mongo_id: str | None) -> str | None:
    if not mongo_id:
        return None
    stmt = select(model).where(model.mongo_id == mongo_id)
    obj = session.execute(stmt).scalar_one_or_none()
    return getattr(obj, id_attr) if obj else None


def _delete_missing(session: Session, model: Any, mongo_ids: set[str]) -> int:
    if not hasattr(model, "mongo_id"):
        return 0
    rows = session.execute(select(model).where(model.mongo_id.is_not(None))).scalars().all()
    deleted = 0
    for row in rows:
        if str(row.mongo_id) not in mongo_ids:
            session.delete(row)
            deleted += 1
    return deleted


def _sync_class_pg(session: Session, doc: dict[str, Any]) -> str:
    class_id = _stable_id("class", doc, doc.get("class_name"))
    _upsert_by_pk(
        session,
        pgm.EduClass,
        "class_id",
        class_id,
        {
            "class_name": str(doc.get("class_name") or doc.get("name") or "").strip(),
            "mongo_id": _oid(doc.get("_id")),
            "import_key": doc.get("import_key"),
            "is_deleted": bool(doc.get("is_deleted", False)),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return class_id


def _sync_subject_pg(db: Any, session: Session, doc: dict[str, Any]) -> str:
    class_mongo_id = _oid(doc.get("class_id"))
    class_id = _get_pg_id_by_mongo(session, pgm.EduClass, "class_id", class_mongo_id)
    if not class_id:
        parent = _mongo_doc(db, "class", doc.get("class_id"))
        if parent and not parent.get("is_deleted"):
            class_id = _sync_class_pg(session, parent)
    if not class_id:
        raise ValueError(f"subject parent class not found: mongo_id={class_mongo_id}")
    subject_id = _stable_id("subject", doc, doc.get("subject_name"))
    _upsert_by_pk(
        session,
        pgm.Subject,
        "subject_id",
        subject_id,
        {
            "class_id": class_id,
            "subject_name": str(doc.get("subject_name") or doc.get("name") or "").strip(),
            "subject_type": str(doc.get("subject_type") or doc.get("type") or "").strip() or None,
            "bucket_name": doc.get("bucket_name"),
            "asset_prefixes": _jsonable(doc.get("asset_prefixes")),
            "mongo_id": _oid(doc.get("_id")),
            "import_key": doc.get("import_key"),
            "is_deleted": bool(doc.get("is_deleted", False)),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return subject_id


def _sync_topic_pg(db: Any, session: Session, doc: dict[str, Any]) -> str:
    subject_mongo_id = _oid(doc.get("subject_id"))
    subject_id = _get_pg_id_by_mongo(session, pgm.Subject, "subject_id", subject_mongo_id)
    if not subject_id:
        parent = _mongo_doc(db, "subject", doc.get("subject_id"))
        if parent and not parent.get("is_deleted"):
            subject_id = _sync_subject_pg(db, session, parent)
    if not subject_id:
        raise ValueError(f"topic parent subject not found: mongo_id={subject_mongo_id}")
    topic_id = _stable_id("topic", doc, doc.get("topic_name"), doc.get("topic_num"))
    _upsert_by_pk(
        session,
        pgm.Topic,
        "topic_id",
        topic_id,
        {
            "subject_id": subject_id,
            "topic_num": int(doc.get("topic_num") or 0),
            "topic_name": str(doc.get("topic_name") or doc.get("name") or "").strip(),
            "asset_prefixes": _jsonable(doc.get("asset_prefixes")),
            "mongo_id": _oid(doc.get("_id")),
            "import_key": doc.get("import_key"),
            "is_deleted": bool(doc.get("is_deleted", False)),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return topic_id


def _sync_lesson_pg(db: Any, session: Session, doc: dict[str, Any]) -> str:
    topic_mongo_id = _oid(doc.get("topic_id"))
    topic_id = _get_pg_id_by_mongo(session, pgm.Topic, "topic_id", topic_mongo_id)
    if not topic_id:
        parent = _mongo_doc(db, "topic", doc.get("topic_id"))
        if parent and not parent.get("is_deleted"):
            topic_id = _sync_topic_pg(db, session, parent)
    if not topic_id:
        raise ValueError(f"lesson parent topic not found: mongo_id={topic_mongo_id}")
    lesson_id = _stable_id("lesson", doc, doc.get("lesson_name"), doc.get("lesson_num"))
    _upsert_by_pk(
        session,
        pgm.Lesson,
        "lesson_id",
        lesson_id,
        {
            "topic_id": topic_id,
            "lesson_num": int(doc.get("lesson_num") or 0),
            "lesson_name": str(doc.get("lesson_name") or doc.get("name") or "").strip(),
            "lesson_type": doc.get("lesson_type"),
            "asset_prefixes": _jsonable(doc.get("asset_prefixes")),
            "mongo_id": _oid(doc.get("_id")),
            "import_key": doc.get("import_key"),
            "is_deleted": bool(doc.get("is_deleted", False)),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return lesson_id


def _sync_chunk_pg(db: Any, session: Session, doc: dict[str, Any]) -> str:
    lesson_mongo_id = _oid(doc.get("lesson_id"))
    lesson_id = _get_pg_id_by_mongo(session, pgm.Lesson, "lesson_id", lesson_mongo_id)
    if not lesson_id:
        parent = _mongo_doc(db, "lesson", doc.get("lesson_id"))
        if parent and not parent.get("is_deleted"):
            lesson_id = _sync_lesson_pg(db, session, parent)
    if not lesson_id:
        raise ValueError(f"chunk parent lesson not found: mongo_id={lesson_mongo_id}")
    chunk_id = _stable_id("chunk", doc, doc.get("chunk_name"), doc.get("chunk_num"))
    _upsert_by_pk(
        session,
        pgm.Chunk,
        "chunk_id",
        chunk_id,
        {
            "lesson_id": lesson_id,
            "chunk_num": int(doc.get("chunk_num") or 0),
            "chunk_name": str(doc.get("chunk_name") or doc.get("name") or "").strip(),
            "asset_prefixes": _jsonable(doc.get("asset_prefixes")),
            "mongo_id": _oid(doc.get("_id")),
            "import_key": doc.get("import_key"),
            "is_deleted": bool(doc.get("is_deleted", False)),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return chunk_id


def sync_chunk_to_postgres(
    *,
    mongo_id: str | None = None,
    import_key: str | None = None,
    create_schema: bool = True,
) -> dict[str, Any]:
    if create_schema:
        ensure_postgres_schema()

    settings = get_settings()
    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        db = client[validate_safe_mongo_db_name(settings.mongo_db_name)]
        chunk_doc = None
        if mongo_id:
            chunk_doc = _mongo_doc(db, "chunk", mongo_id)
        elif import_key:
            chunk_doc = db.chunk.find_one({"import_key": import_key})
        if not chunk_doc:
            raise FileNotFoundError("Chunk document not found in MongoDB.")
        with postgres_session() as session:
            chunk_id = _sync_chunk_pg(db, session, chunk_doc)
            session.commit()
        return {"ok": True, "chunk_id": chunk_id}
    finally:
        client.close()


def sync_chunk_to_neo4j(
    *,
    chunk_id: str,
    create_schema: bool = True,
    rebuild_neo4j: bool = False,
) -> dict[str, Any]:
    if create_schema:
        ensure_neo4j_schema(enable_topic_vector_index=True)
    if rebuild_neo4j:
        clear_learning_graph()
        if create_schema:
            ensure_neo4j_schema(enable_topic_vector_index=True)

    counts = {
        "class_count": 0,
        "subject_count": 0,
        "topic_count": 0,
        "lesson_count": 0,
        "chunk_count": 0,
        "keyword_relation_count": 0,
    }

    with postgres_session() as pg_session:
        chunk = pg_session.get(pgm.Chunk, chunk_id)
        if chunk is None:
            raise FileNotFoundError(f"Postgres chunk not found: {chunk_id}")
        lesson = pg_session.get(pgm.Lesson, chunk.lesson_id)
        if lesson is None:
            raise ValueError(f"Postgres lesson not found for chunk: {chunk_id}")
        topic = pg_session.get(pgm.Topic, lesson.topic_id)
        if topic is None:
            raise ValueError(f"Postgres topic not found for lesson: {lesson.lesson_id}")
        subject = pg_session.get(pgm.Subject, topic.subject_id)
        if subject is None:
            raise ValueError(f"Postgres subject not found for topic: {topic.topic_id}")
        edu_class = pg_session.get(pgm.EduClass, subject.class_id)
        if edu_class is None:
            raise ValueError(f"Postgres class not found for subject: {subject.subject_id}")
        embedding = pg_session.get(pgm.TopicEmbedding, topic.topic_id)
        keyword_ids = [relation.keyword_id for relation in pg_session.execute(select(pgm.ChunkKeyword).where(pgm.ChunkKeyword.chunk_id == chunk_id)).scalars().all()]
        keywords = pg_session.execute(select(pgm.Keyword).where(pgm.Keyword.keyword_id.in_(keyword_ids))).scalars().all() if keyword_ids else []
        keyword_by_id = {keyword.keyword_id: keyword for keyword in keywords}

        with neo4j_session() as neo_session_obj:
            upsert_class(
                neo_session_obj,
                {
                    "class_id": edu_class.class_id,
                    "class_name": edu_class.class_name,
                    "mongo_id": edu_class.mongo_id,
                    "import_key": edu_class.import_key,
                },
            )
            counts["class_count"] += 1

            upsert_subject(
                neo_session_obj,
                {
                    "subject_id": subject.subject_id,
                    "class_id": subject.class_id,
                    "subject_name": subject.subject_name,
                    "subject_type": subject.subject_type,
                    "mongo_id": subject.mongo_id,
                    "import_key": subject.import_key,
                },
            )
            counts["subject_count"] += 1

            upsert_topic(
                neo_session_obj,
                {
                    "topic_id": topic.topic_id,
                    "subject_id": topic.subject_id,
                    "topic_num": topic.topic_num,
                    "topic_name": topic.topic_name,
                    "mongo_id": topic.mongo_id,
                    "import_key": topic.import_key,
                    "embedding": embedding.embedding if embedding else None,
                    "embedding_text": embedding.embedding_text if embedding else None,
                    "embedding_model": embedding.model_name if embedding else None,
                },
            )
            counts["topic_count"] += 1

            upsert_lesson(
                neo_session_obj,
                {
                    "lesson_id": lesson.lesson_id,
                    "topic_id": lesson.topic_id,
                    "lesson_num": lesson.lesson_num,
                    "lesson_name": lesson.lesson_name,
                    "lesson_type": lesson.lesson_type,
                    "mongo_id": lesson.mongo_id,
                    "import_key": lesson.import_key,
                },
            )
            counts["lesson_count"] += 1

            upsert_chunk(
                neo_session_obj,
                {
                    "chunk_id": chunk.chunk_id,
                    "lesson_id": chunk.lesson_id,
                    "chunk_num": chunk.chunk_num,
                    "chunk_name": chunk.chunk_name,
                    "mongo_id": chunk.mongo_id,
                    "import_key": chunk.import_key,
                },
            )
            counts["chunk_count"] += 1

            for relation in pg_session.execute(select(pgm.ChunkKeyword).where(pgm.ChunkKeyword.chunk_id == chunk_id)).scalars().all():
                keyword = keyword_by_id.get(relation.keyword_id)
                if not keyword:
                    continue
                keyword_key = f"{relation.chunk_id}::{keyword.keyword_slug}"
                upsert_chunk_keyword(
                    neo_session_obj,
                    {
                        "chunk_id": relation.chunk_id,
                        "keyword_key": keyword_key,
                        "keyword_id": keyword.keyword_id,
                        "keyword_name": keyword.keyword_name,
                        "keyword_slug": keyword.keyword_slug,
                        "mongo_id": relation.mongo_id,
                    },
                )
                counts["keyword_relation_count"] += 1

    return {"ok": True, "chunk_id": chunk_id, "counts": counts}


def _sync_keyword_pg(session: Session, doc: dict[str, Any]) -> str:
    keyword_id = _keyword_id(doc)
    _upsert_by_pk(
        session,
        pgm.Keyword,
        "keyword_id",
        keyword_id,
        {
            "keyword_name": str(doc.get("keyword_name") or doc.get("name") or "").strip(),
            "keyword_slug": str(doc.get("keyword_slug") or _slugify(doc.get("keyword_name"))).strip(),
            "aliases": _jsonable(doc.get("aliases")),
            "asset_prefixes": _jsonable(doc.get("asset_prefixes")),
            "mongo_id": _oid(doc.get("_id")),
            "import_key": doc.get("import_key"),
            "is_deleted": bool(doc.get("is_deleted", False)),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return keyword_id


def _sync_keyword_alias_pg(db: Any, session: Session, doc: dict[str, Any]) -> str:
    keyword_mongo_id = _oid(doc.get("keyword_id"))
    keyword_id = _get_pg_id_by_mongo(session, pgm.Keyword, "keyword_id", keyword_mongo_id)
    if not keyword_id:
        parent = _mongo_doc(db, "keyword", doc.get("keyword_id"))
        if parent and not parent.get("is_deleted"):
            keyword_id = _sync_keyword_pg(session, parent)
    if not keyword_id:
        raise ValueError(f"keyword_alias parent keyword not found: mongo_id={keyword_mongo_id}")
    alias_id = _stable_id("alias", doc, keyword_id, doc.get("alias_norm") or doc.get("alias_name"))
    _upsert_by_pk(
        session,
        pgm.KeywordAlias,
        "alias_id",
        alias_id,
        {
            "keyword_id": keyword_id,
            "keyword_name": doc.get("keyword_name"),
            "alias_name": str(doc.get("alias_name") or "").strip(),
            "alias_norm": str(doc.get("alias_norm") or _slugify(doc.get("alias_name"))).strip(),
            "mongo_id": _oid(doc.get("_id")),
        },
    )
    return alias_id


def _sync_chunk_keyword_pg(db: Any, session: Session, doc: dict[str, Any]) -> tuple[str, str]:
    chunk_mongo_id = _oid(doc.get("chunk_id"))
    keyword_mongo_id = _oid(doc.get("keyword_id"))
    chunk_id = _get_pg_id_by_mongo(session, pgm.Chunk, "chunk_id", chunk_mongo_id)
    keyword_id = _get_pg_id_by_mongo(session, pgm.Keyword, "keyword_id", keyword_mongo_id)
    if not chunk_id:
        parent_chunk = _mongo_doc(db, "chunk", doc.get("chunk_id"))
        if parent_chunk and not parent_chunk.get("is_deleted"):
            chunk_id = _sync_chunk_pg(db, session, parent_chunk)
    if not keyword_id:
        parent_keyword = _mongo_doc(db, "keyword", doc.get("keyword_id"))
        if parent_keyword and not parent_keyword.get("is_deleted"):
            keyword_id = _sync_keyword_pg(session, parent_keyword)
    if not chunk_id or not keyword_id:
        raise ValueError(f"chunk_keyword missing mapped refs: chunk={chunk_mongo_id}, keyword={keyword_mongo_id}")
    existing = session.get(pgm.ChunkKeyword, {"chunk_id": chunk_id, "keyword_id": keyword_id})
    if existing is None:
        existing = pgm.ChunkKeyword(chunk_id=chunk_id, keyword_id=keyword_id)
        session.add(existing)
    existing.mongo_id = _oid(doc.get("_id"))
    existing.synced_at = _now()
    session.flush()
    return chunk_id, keyword_id


def _sync_topic_bag_pg(db: Any, session: Session, doc: dict[str, Any]) -> str:
    topic_mongo_id = _oid(doc.get("topic_id"))
    topic_id = _get_pg_id_by_mongo(session, pgm.Topic, "topic_id", topic_mongo_id)
    if not topic_id:
        parent = _mongo_doc(db, "topic", doc.get("topic_id"))
        if parent and not parent.get("is_deleted"):
            topic_id = _sync_topic_pg(db, session, parent)
    if not topic_id:
        raise ValueError(f"topic_bag parent topic not found: mongo_id={topic_mongo_id}")
    bag_id = _stable_id("topic_bag", doc, topic_id)
    _upsert_by_pk(
        session,
        pgm.TopicBag,
        "topic_bag_id",
        bag_id,
        {
            "topic_id": topic_id,
            "topic_name": doc.get("topic_name"),
            "keyword_refs": _jsonable(doc.get("keyword_refs")),
            "total_keywords": int(doc.get("total_keywords") or 0),
            "keyword_embedding_text": doc.get("keyword_embedding_text"),
            "mongo_id": _oid(doc.get("_id")),
            "is_deleted": bool(doc.get("is_deleted", False)),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return bag_id


def _sync_asset_pg(session: Session, doc: dict[str, Any], owner_id_map: dict[tuple[str, str], str]) -> str:
    owner_type = str(doc.get("owner_type") or "").strip()
    owner_mongo_id = _oid(doc.get("owner_id"))
    owner_id = owner_id_map.get((owner_type, owner_mongo_id or ""))
    asset_id = _stable_id("asset", doc, doc.get("object_key"))
    _upsert_by_pk(
        session,
        pgm.Asset,
        "asset_id",
        asset_id,
        {
            "owner_type": owner_type,
            "owner_id": owner_id,
            "asset_type": doc.get("asset_type"),
            "bucket": doc.get("bucket"),
            "path_prefix": doc.get("path_prefix"),
            "object_key": str(doc.get("object_key") or "").strip(),
            "file_name": doc.get("file_name"),
            "url": doc.get("url"),
            "content_type": doc.get("content_type"),
            "size": doc.get("size"),
            "mongo_id": _oid(doc.get("_id")),
            "import_key": doc.get("import_key"),
            "is_deleted": bool(doc.get("is_deleted", False)),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return asset_id


def _sync_import_job_pg(session: Session, doc: dict[str, Any]) -> str:
    import_job_id = _stable_id("import_job", doc, doc.get("job_id"))
    _upsert_by_pk(
        session,
        pgm.ImportJob,
        "import_job_id",
        import_job_id,
        {
            "job_id": doc.get("job_id"),
            "book_stem": doc.get("book_stem"),
            "bundle_path": doc.get("bundle_path"),
            "status": doc.get("status"),
            "schema_name": doc.get("schema"),
            "upload_minio": doc.get("upload_minio"),
            "counts": _jsonable(doc.get("counts")),
            "errors": _jsonable(doc.get("errors")),
            "mongo_id": _oid(doc.get("_id")),
            "import_key": doc.get("import_key"),
            "started_at": doc.get("started_at"),
            "completed_at": doc.get("completed_at"),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        },
    )
    return import_job_id


def _build_owner_id_map(session: Session) -> dict[tuple[str, str], str]:
    mapping: dict[tuple[str, str], str] = {}
    specs = [
        ("subject", pgm.Subject, "subject_id"),
        ("topic", pgm.Topic, "topic_id"),
        ("lesson", pgm.Lesson, "lesson_id"),
        ("chunk", pgm.Chunk, "chunk_id"),
    ]
    for owner_type, model, id_attr in specs:
        rows = session.execute(select(model)).scalars().all()
        for row in rows:
            if getattr(row, "mongo_id", None):
                mapping[(owner_type, str(row.mongo_id))] = getattr(row, id_attr)
    return mapping


def _topic_embedding_text(db: Any, topic_doc: dict[str, Any]) -> str:
    bag = db.topic_bag.find_one({"topic_id": topic_doc.get("_id"), "is_deleted": {"$ne": True}})
    if bag:
        text = normalize_embedding_text(bag.get("keyword_embedding_text"))
        if text:
            return text
        refs = bag.get("keyword_refs") or []
        names = [str(item.get("keyword_name") or "").strip() for item in refs if isinstance(item, dict)]
        text = normalize_embedding_text(" | ".join(name for name in names if name))
        if text:
            return text

    keyword_names: list[str] = []
    lessons = list(db.lesson.find({"topic_id": topic_doc.get("_id"), "is_deleted": {"$ne": True}}, {"_id": 1}))
    lesson_ids = [item["_id"] for item in lessons]
    if lesson_ids:
        chunks = list(db.chunk.find({"lesson_id": {"$in": lesson_ids}, "is_deleted": {"$ne": True}}, {"_id": 1}))
        chunk_ids = [item["_id"] for item in chunks]
        if chunk_ids:
            for ck in db.chunk_keyword.find({"chunk_id": {"$in": chunk_ids}, "is_deleted": {"$ne": True}}):
                kw = _mongo_doc(db, "keyword", ck.get("keyword_id"))
                if kw and not kw.get("is_deleted") and kw.get("keyword_name"):
                    keyword_names.append(str(kw["keyword_name"]))
    if keyword_names:
        deduped = sorted(set(keyword_names), key=lambda item: item.lower())
        return normalize_embedding_text(" | ".join(deduped))
    return normalize_embedding_text(topic_doc.get("topic_name"))


def _upsert_topic_embedding(session: Session, topic_id: str, text: str, embedding: list[float]) -> None:
    obj = session.get(pgm.TopicEmbedding, topic_id)
    if obj is None:
        obj = pgm.TopicEmbedding(topic_id=topic_id)
        session.add(obj)
    obj.embedding = embedding
    obj.embedding_text = text
    obj.model_name = MODEL_SHORT
    obj.updated_at = _now()
    session.flush()


def sync_mongo_to_postgres(
    *,
    job_id: str | None = None,
    create_schema: bool = True,
    prune_missing: bool = False,
    enable_embeddings: bool = True,
) -> dict[str, Any]:
    if create_schema:
        ensure_postgres_schema()

    settings = get_settings()
    from pymongo import MongoClient

    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[validate_safe_mongo_db_name(settings.mongo_db_name)]

    counts = {f"{name}_count": 0 for name in CORE_ORDER}
    counts.update({"topic_embedding_count": 0, "deleted_pruned_count": 0})
    errors: list[dict[str, Any]] = []
    active_mongo_ids: dict[str, set[str]] = {name: set() for name in CORE_ORDER}

    with postgres_session() as session:
        try:
            # Parent-first sync.
            for doc in _active_docs(db, "class"):
                active_mongo_ids["class"].add(_oid(doc.get("_id")) or "")
                _sync_class_pg(session, doc)
                counts["class_count"] += 1
            session.commit()

            for doc in _active_docs(db, "subject"):
                active_mongo_ids["subject"].add(_oid(doc.get("_id")) or "")
                _sync_subject_pg(db, session, doc)
                counts["subject_count"] += 1
            session.commit()

            for doc in _active_docs(db, "topic"):
                active_mongo_ids["topic"].add(_oid(doc.get("_id")) or "")
                topic_id = _sync_topic_pg(db, session, doc)
                counts["topic_count"] += 1
                if enable_embeddings:
                    text = _topic_embedding_text(db, doc)
                    if text:
                        embedding = embed_passage(text)
                        if embedding:
                            _upsert_topic_embedding(session, topic_id, text, embedding)
                            counts["topic_embedding_count"] += 1
            session.commit()

            for doc in _active_docs(db, "lesson"):
                active_mongo_ids["lesson"].add(_oid(doc.get("_id")) or "")
                _sync_lesson_pg(db, session, doc)
                counts["lesson_count"] += 1
            session.commit()

            for doc in _active_docs(db, "chunk"):
                active_mongo_ids["chunk"].add(_oid(doc.get("_id")) or "")
                _sync_chunk_pg(db, session, doc)
                counts["chunk_count"] += 1
            session.commit()

            for doc in _active_docs(db, "keyword"):
                active_mongo_ids["keyword"].add(_oid(doc.get("_id")) or "")
                _sync_keyword_pg(session, doc)
                counts["keyword_count"] += 1
            session.commit()

            for doc in _active_docs(db, "keyword_alias"):
                active_mongo_ids["keyword_alias"].add(_oid(doc.get("_id")) or "")
                _sync_keyword_alias_pg(db, session, doc)
                counts["keyword_alias_count"] += 1
            session.commit()

            for doc in _active_docs(db, "chunk_keyword"):
                active_mongo_ids["chunk_keyword"].add(_oid(doc.get("_id")) or "")
                _sync_chunk_keyword_pg(db, session, doc)
                counts["chunk_keyword_count"] += 1
            session.commit()

            for doc in _active_docs(db, "topic_bag"):
                active_mongo_ids["topic_bag"].add(_oid(doc.get("_id")) or "")
                _sync_topic_bag_pg(db, session, doc)
                counts["topic_bag_count"] += 1
            session.commit()

            owner_id_map = _build_owner_id_map(session)
            for doc in _active_docs(db, "asset"):
                active_mongo_ids["asset"].add(_oid(doc.get("_id")) or "")
                if not doc.get("object_key"):
                    continue
                _sync_asset_pg(session, doc, owner_id_map)
                counts["asset_count"] += 1
            session.commit()

            for doc in _active_docs(db, "import_job"):
                active_mongo_ids["import_job"].add(_oid(doc.get("_id")) or "")
                _sync_import_job_pg(session, doc)
                counts["import_job_count"] += 1
            session.commit()

            if prune_missing:
                # Child-first pruning to satisfy FK constraints.
                prune_specs = [
                    (pgm.Asset, "asset"),
                    (pgm.TopicBag, "topic_bag"),
                    (pgm.KeywordAlias, "keyword_alias"),
                    (pgm.Keyword, "keyword"),
                    (pgm.Chunk, "chunk"),
                    (pgm.Lesson, "lesson"),
                    (pgm.Topic, "topic"),
                    (pgm.Subject, "subject"),
                    (pgm.EduClass, "class"),
                    (pgm.ImportJob, "import_job"),
                ]
                for model, collection in prune_specs:
                    counts["deleted_pruned_count"] += _delete_missing(session, model, active_mongo_ids.get(collection, set()))
                session.commit()

            sync_id = f"sync_{hashlib.sha1((job_id or utc_now_iso()).encode()).hexdigest()[:16]}"
            _upsert_by_pk(
                session,
                pgm.SyncRun,
                "sync_run_id",
                sync_id,
                {
                    "job_id": job_id,
                    "target": "postgres",
                    "status": "completed",
                    "counts": counts,
                    "errors": errors,
                    "started_at": _now(),
                    "completed_at": _now(),
                },
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            errors.append({"target": "postgres", "error": str(exc)})
            raise
        finally:
            client.close()

    return {"ok": not errors, "target": "postgres", "counts": counts, "errors": errors}


def _rows_for_neo(session: Session) -> dict[str, list[Any]]:
    return {
        "class": session.execute(select(pgm.EduClass).where(pgm.EduClass.is_deleted.is_(False))).scalars().all(),
        "subject": session.execute(select(pgm.Subject).where(pgm.Subject.is_deleted.is_(False))).scalars().all(),
        "topic": session.execute(select(pgm.Topic).where(pgm.Topic.is_deleted.is_(False))).scalars().all(),
        "lesson": session.execute(select(pgm.Lesson).where(pgm.Lesson.is_deleted.is_(False))).scalars().all(),
        "chunk": session.execute(select(pgm.Chunk).where(pgm.Chunk.is_deleted.is_(False))).scalars().all(),
        "chunk_keyword": session.execute(select(pgm.ChunkKeyword)).scalars().all(),
        "keyword": session.execute(select(pgm.Keyword).where(pgm.Keyword.is_deleted.is_(False))).scalars().all(),
        "topic_embedding": session.execute(select(pgm.TopicEmbedding)).scalars().all(),
    }


def sync_postgres_to_neo4j(*, create_schema: bool = True, rebuild_neo4j: bool = False) -> dict[str, Any]:
    if create_schema:
        ensure_neo4j_schema(enable_topic_vector_index=True)
    if rebuild_neo4j:
        clear_learning_graph()
        if create_schema:
            ensure_neo4j_schema(enable_topic_vector_index=True)

    counts = {
        "class_count": 0,
        "subject_count": 0,
        "topic_count": 0,
        "lesson_count": 0,
        "chunk_count": 0,
        "keyword_relation_count": 0,
    }
    errors: list[dict[str, Any]] = []

    with postgres_session() as pg_session:
        rows = _rows_for_neo(pg_session)
        embedding_by_topic = {row.topic_id: row for row in rows["topic_embedding"]}
        keyword_by_id = {row.keyword_id: row for row in rows["keyword"]}

        with neo4j_session() as neo_session_obj:
            for row in rows["class"]:
                upsert_class(
                    neo_session_obj,
                    {
                        "class_id": row.class_id,
                        "class_name": row.class_name,
                        "mongo_id": row.mongo_id,
                        "import_key": row.import_key,
                    },
                )
                counts["class_count"] += 1

            for row in rows["subject"]:
                upsert_subject(
                    neo_session_obj,
                    {
                        "subject_id": row.subject_id,
                        "class_id": row.class_id,
                        "subject_name": row.subject_name,
                        "subject_type": row.subject_type,
                        "mongo_id": row.mongo_id,
                        "import_key": row.import_key,
                    },
                )
                counts["subject_count"] += 1

            for row in rows["topic"]:
                embedding = embedding_by_topic.get(row.topic_id)
                upsert_topic(
                    neo_session_obj,
                    {
                        "topic_id": row.topic_id,
                        "subject_id": row.subject_id,
                        "topic_num": row.topic_num,
                        "topic_name": row.topic_name,
                        "mongo_id": row.mongo_id,
                        "import_key": row.import_key,
                        "embedding": embedding.embedding if embedding else None,
                        "embedding_text": embedding.embedding_text if embedding else None,
                        "embedding_model": embedding.model_name if embedding else None,
                    },
                )
                counts["topic_count"] += 1

            for row in rows["lesson"]:
                upsert_lesson(
                    neo_session_obj,
                    {
                        "lesson_id": row.lesson_id,
                        "topic_id": row.topic_id,
                        "lesson_num": row.lesson_num,
                        "lesson_name": row.lesson_name,
                        "lesson_type": row.lesson_type,
                        "mongo_id": row.mongo_id,
                        "import_key": row.import_key,
                    },
                )
                counts["lesson_count"] += 1

            for row in rows["chunk"]:
                upsert_chunk(
                    neo_session_obj,
                    {
                        "chunk_id": row.chunk_id,
                        "lesson_id": row.lesson_id,
                        "chunk_num": row.chunk_num,
                        "chunk_name": row.chunk_name,
                        "mongo_id": row.mongo_id,
                        "import_key": row.import_key,
                    },
                )
                counts["chunk_count"] += 1

            for relation in rows["chunk_keyword"]:
                keyword = keyword_by_id.get(relation.keyword_id)
                if not keyword:
                    continue
                keyword_key = f"{relation.chunk_id}::{keyword.keyword_slug}"
                upsert_chunk_keyword(
                    neo_session_obj,
                    {
                        "chunk_id": relation.chunk_id,
                        "keyword_key": keyword_key,
                        "keyword_id": keyword.keyword_id,
                        "keyword_name": keyword.keyword_name,
                        "keyword_slug": keyword.keyword_slug,
                        "mongo_id": relation.mongo_id,
                    },
                )
                counts["keyword_relation_count"] += 1

    return {"ok": not errors, "target": "neo4j", "counts": counts, "errors": errors}


def sync_metadata(
    *,
    job_id: str | None = None,
    targets: list[str] | None = None,
    create_schema: bool = True,
    rebuild_neo4j: bool = False,
    prune_missing: bool = False,
    enable_embeddings: bool = True,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    requested = targets or ["all"]
    normalized = {"postgres", "neo4j"} if "all" in requested else set(requested)
    result_counts: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    target_results: dict[str, Any] = {}

    if "postgres" in normalized:
        try:
            pg_result = sync_mongo_to_postgres(
                job_id=job_id,
                create_schema=create_schema,
                prune_missing=prune_missing,
                enable_embeddings=enable_embeddings,
            )
            target_results["postgres"] = pg_result
            result_counts.update({f"postgres_{k}": v for k, v in pg_result.get("counts", {}).items()})
            errors.extend(pg_result.get("errors", []))
        except Exception as exc:
            errors.append({"target": "postgres", "error": str(exc)})

    if "neo4j" in normalized and not any(error.get("target") == "postgres" for error in errors):
        try:
            neo_result = sync_postgres_to_neo4j(create_schema=create_schema, rebuild_neo4j=rebuild_neo4j)
            target_results["neo4j"] = neo_result
            result_counts.update({f"neo4j_{k}": v for k, v in neo_result.get("counts", {}).items()})
            errors.extend(neo_result.get("errors", []))
        except Exception as exc:
            errors.append({"target": "neo4j", "error": str(exc)})

    return {
        "ok": len(errors) == 0,
        "job_id": job_id,
        "targets": sorted(normalized),
        "counts": result_counts,
        "errors": errors,
        "results": target_results,
        "started_at": started_at,
        "completed_at": utc_now_iso(),
    }
