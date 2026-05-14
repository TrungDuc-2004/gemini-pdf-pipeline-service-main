from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, JSON, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EduClass(Base):
    __tablename__ = "class"

    class_id = Column(String, primary_key=True)
    class_name = Column(String, nullable=False)
    mongo_id = Column(String, unique=True, nullable=False, index=True)
    import_key = Column(String, unique=True, nullable=True, index=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class Subject(Base):
    __tablename__ = "subject"
    __table_args__ = (
        CheckConstraint("subject_category IN ('document', 'image', 'video')", name="chk_subject_category"),
    )

    subject_id = Column(String, primary_key=True)
    class_id = Column(String, ForeignKey("class.class_id", ondelete="CASCADE"), nullable=False, index=True)

    subject_name = Column(String, nullable=False)
    subject_category = Column(String(16), nullable=False, default="document")
    subject_type = Column(String, nullable=True)
    bucket_name = Column(String, nullable=True)
    mongo_id = Column(String, unique=True, nullable=False, index=True)
    import_key = Column(String, unique=True, nullable=True, index=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class Topic(Base):
    __tablename__ = "topic"
    __table_args__ = (
        CheckConstraint("topic_category IN ('document', 'image', 'video')", name="chk_topic_category"),
        UniqueConstraint("subject_id", "topic_num", name="uq_topic_num_per_subject"),
    )

    topic_id = Column(String, primary_key=True)
    subject_id = Column(String, ForeignKey("subject.subject_id", ondelete="CASCADE"), nullable=False, index=True)

    topic_num = Column(Integer, nullable=False)
    topic_name = Column(String, nullable=False)
    topic_category = Column(String(16), nullable=False, default="document")
    mongo_id = Column(String, unique=True, nullable=False, index=True)
    import_key = Column(String, unique=True, nullable=True, index=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class Lesson(Base):
    __tablename__ = "lesson"
    __table_args__ = (
        CheckConstraint("lesson_category IN ('document', 'image', 'video')", name="chk_lesson_category"),
        UniqueConstraint("topic_id", "lesson_num", name="uq_lesson_num_per_topic"),
    )

    lesson_id = Column(String, primary_key=True)
    topic_id = Column(String, ForeignKey("topic.topic_id", ondelete="CASCADE"), nullable=False, index=True)

    lesson_num = Column(Integer, nullable=False)
    lesson_name = Column(String, nullable=False)
    lesson_category = Column(String(16), nullable=False, default="document")
    lesson_type = Column(String, nullable=True)
    mongo_id = Column(String, unique=True, nullable=False, index=True)
    import_key = Column(String, unique=True, nullable=True, index=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class Chunk(Base):
    __tablename__ = "chunk"
    __table_args__ = (
        CheckConstraint("chunk_category IN ('document', 'image', 'video')", name="chk_chunk_category"),
        UniqueConstraint("lesson_id", "chunk_num", name="uq_chunk_num_per_lesson"),
    )

    chunk_id = Column(String, primary_key=True)
    lesson_id = Column(String, ForeignKey("lesson.lesson_id", ondelete="CASCADE"), nullable=False, index=True)

    chunk_num = Column(Integer, nullable=False)
    chunk_name = Column(String, nullable=False)
    chunk_category = Column(String(16), nullable=False, default="document")
    chunk_type = Column(String(32), nullable=True)
    mongo_id = Column(String, unique=True, nullable=False, index=True)
    import_key = Column(String, unique=True, nullable=True, index=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class Keyword(Base):
    __tablename__ = "keyword"

    keyword_id = Column(String, primary_key=True)
    keyword_name = Column(String, nullable=False)
    keyword_slug = Column(String, nullable=False, unique=True, index=True)
    chunk_id = Column(String, ForeignKey("chunk.chunk_id", ondelete="CASCADE"), nullable=True, index=True)
    keyword_embedding = Column(JSON, nullable=True)
    embedding_provider = Column(String, nullable=True)
    aliases = Column(JSON, nullable=True)
    mongo_id = Column(String, unique=True, nullable=False, index=True)
    import_key = Column(String, unique=True, nullable=True, index=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class KeywordAlias(Base):
    __tablename__ = "keyword_alias"

    alias_id = Column(String, primary_key=True)
    keyword_id = Column(String, ForeignKey("keyword.keyword_id", ondelete="CASCADE"), nullable=False, index=True)

    keyword_name = Column(String, nullable=True)
    alias_name = Column(String, nullable=False)
    alias_norm = Column(String, nullable=False, index=True)
    mongo_id = Column(String, unique=True, nullable=True, index=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ChunkKeyword(Base):
    __tablename__ = "chunk_keyword"

    chunk_id = Column(String, ForeignKey("chunk.chunk_id", ondelete="CASCADE"), primary_key=True)
    keyword_id = Column(String, ForeignKey("keyword.keyword_id", ondelete="CASCADE"), primary_key=True)
    mongo_id = Column(String, unique=True, nullable=True, index=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class TopicBag(Base):
    __tablename__ = "topic_bag"

    topic_bag_id = Column(String, primary_key=True)
    topic_id = Column(String, ForeignKey("topic.topic_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    topic_name = Column(String, nullable=True)
    keyword_refs = Column(JSON, nullable=True)
    total_keywords = Column(Integer, nullable=False, default=0)
    keyword_embedding_text = Column(Text, nullable=True)
    mongo_id = Column(String, unique=True, nullable=True, index=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class TopicEmbedding(Base):
    __tablename__ = "topic_embedding"

    topic_id = Column(String, ForeignKey("topic.topic_id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(JSON, nullable=False)
    embedding_text = Column(Text, nullable=True)
    model_name = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ImportJob(Base):
    __tablename__ = "import_job"

    import_job_id = Column(String, primary_key=True)
    job_id = Column(String, nullable=True, index=True)
    book_stem = Column(String, nullable=True)
    bundle_path = Column(Text, nullable=True)
    status = Column(String, nullable=True)
    schema_name = Column(String, nullable=True)
    upload_minio = Column(Boolean, nullable=True)
    counts = Column(JSON, nullable=True)
    errors = Column(JSON, nullable=True)

    mongo_id = Column(String, unique=True, nullable=True, index=True)
    import_key = Column(String, unique=True, nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class SyncRun(Base):
    __tablename__ = "sync_run"

    sync_run_id = Column(String, primary_key=True)
    job_id = Column(String, nullable=True, index=True)
    target = Column(String, nullable=False)
    status = Column(String, nullable=False)
    counts = Column(JSON, nullable=True)
    errors = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
