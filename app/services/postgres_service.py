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
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_pg_chunk_lesson_num ON "chunk" (lesson_id, chunk_num)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_pg_topic_subject_num ON topic (subject_id, topic_num)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_pg_lesson_topic_num ON lesson (topic_id, lesson_num)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_pg_asset_owner ON asset (owner_type, owner_id)'))
