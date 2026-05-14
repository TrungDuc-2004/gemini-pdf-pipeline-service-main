from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models.postgres_models import Base


@lru_cache(maxsize=1)
def get_postgres_engine() -> Engine:
    settings = get_settings()
    dsn = settings.postgres_dsn.strip()
    if not dsn:
        if not all([settings.pg_host, settings.pg_port, settings.pg_user, settings.pg_password, settings.pg_name]):
            raise RuntimeError(
                "Missing PostgreSQL config. Set POSTGRES_DSN or PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_NAME."
            )
        dsn = f"postgresql+psycopg2://{settings.pg_user}:{settings.pg_password}@{settings.pg_host}:{settings.pg_port}/{settings.pg_name}"
    return create_engine(dsn, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_postgres_engine(), autocommit=False, autoflush=False, future=True)


@contextmanager
def postgres_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def ensure_postgres_schema() -> None:
    engine = get_postgres_engine()
    Base.metadata.create_all(bind=engine)
    # Extra indexes/constraints that are safe across repeated runs.
    with engine.begin() as conn:
        # Lightweight migrations for databases created before the approve-to-sync schema.
        conn.execute(text("ALTER TABLE subject ADD COLUMN IF NOT EXISTS subject_category VARCHAR(16) NOT NULL DEFAULT 'document'"))
        conn.execute(text("ALTER TABLE topic ADD COLUMN IF NOT EXISTS topic_category VARCHAR(16) NOT NULL DEFAULT 'document'"))
        conn.execute(text("ALTER TABLE lesson ADD COLUMN IF NOT EXISTS lesson_category VARCHAR(16) NOT NULL DEFAULT 'document'"))
        conn.execute(text("ALTER TABLE \"chunk\" ADD COLUMN IF NOT EXISTS chunk_category VARCHAR(16) NOT NULL DEFAULT 'document'"))
        conn.execute(text('ALTER TABLE "chunk" ADD COLUMN IF NOT EXISTS chunk_type VARCHAR(32)'))
        conn.execute(text('ALTER TABLE keyword ADD COLUMN IF NOT EXISTS chunk_id VARCHAR'))
        conn.execute(text('ALTER TABLE keyword ADD COLUMN IF NOT EXISTS keyword_embedding JSON'))
        conn.execute(text('ALTER TABLE keyword ADD COLUMN IF NOT EXISTS embedding_provider VARCHAR'))

        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_pg_chunk_lesson_num ON "chunk" (lesson_id, chunk_num)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_pg_topic_subject_num ON topic (subject_id, topic_num)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_pg_lesson_topic_num ON lesson (topic_id, lesson_num)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_pg_keyword_chunk ON keyword (chunk_id)'))
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_topic_num_per_subject ON topic (subject_id, topic_num)'))
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_lesson_num_per_topic ON lesson (topic_id, lesson_num)'))
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_chunk_num_per_lesson ON "chunk" (lesson_id, chunk_num)'))
