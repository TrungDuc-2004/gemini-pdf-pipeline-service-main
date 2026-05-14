from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neo4j import Session
else:
    Session = Any

from app.core.config import get_settings
from app.services.e5_embedding_service import EMBEDDING_DIMENSIONS

ROOT_THING_ID = "thing"


@lru_cache(maxsize=1)
def get_neo4j_driver():
    settings = get_settings()
    if not settings.neo4j_uri or not settings.neo4j_user or not settings.neo4j_password:
        raise RuntimeError("Missing Neo4j config. Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD.")
    from neo4j import GraphDatabase

    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


@contextmanager
def neo4j_session() -> Iterator[Session]:
    settings = get_settings()
    driver = get_neo4j_driver()
    session = driver.session(database=(settings.neo4j_database or None))
    try:
        yield session
    finally:
        session.close()


def ensure_neo4j_schema(*, enable_topic_vector_index: bool = True) -> None:
    with neo4j_session() as session:
        statements = [
            "CREATE CONSTRAINT thing_id IF NOT EXISTS FOR (n:Thing) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT class_id IF NOT EXISTS FOR (n:Class) REQUIRE n.class_id IS UNIQUE",
            "CREATE CONSTRAINT subject_id IF NOT EXISTS FOR (n:Subject) REQUIRE n.subject_id IS UNIQUE",
            "CREATE CONSTRAINT topic_id IF NOT EXISTS FOR (n:Topic) REQUIRE n.topic_id IS UNIQUE",
            "CREATE CONSTRAINT lesson_id IF NOT EXISTS FOR (n:Lesson) REQUIRE n.lesson_id IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.chunk_id IS UNIQUE",
            "CREATE CONSTRAINT keyword_key IF NOT EXISTS FOR (n:Keyword) REQUIRE n.keyword_key IS UNIQUE",
        ]
        for statement in statements:
            session.run(statement).consume()
        if enable_topic_vector_index:
            session.run(
                f"""
                CREATE VECTOR INDEX topic_embedding_idx IF NOT EXISTS
                FOR (n:Topic) ON (n.embedding)
                OPTIONS {{indexConfig: {{
                    `vector.dimensions`: {EMBEDDING_DIMENSIONS},
                    `vector.similarity_function`: 'cosine'
                }}}}
                """
            ).consume()


def clear_learning_graph() -> None:
    with neo4j_session() as session:
        session.run(
            """
            MATCH (n)
            WHERE n:Thing OR n:Class OR n:Subject OR n:Topic OR n:Lesson OR n:Chunk OR n:Keyword
            DETACH DELETE n
            """
        ).consume()


def _ensure_root(session: Session) -> None:
    session.run(
        """
        MERGE (t:Thing {id: $id})
        ON CREATE SET t.name = 'Thing'
        """,
        id=ROOT_THING_ID,
    ).consume()


def upsert_class(session: Session, row: dict[str, Any]) -> None:
    _ensure_root(session)
    session.run(
        """
        MERGE (c:Class {class_id: $class_id})
        SET c.class_name = $class_name,
            c.mongo_id = $mongo_id,
            c.import_key = $import_key,
            c.updated_at = datetime()
        WITH c
        MATCH (root:Thing {id: $root_id})
        MERGE (root)-[:HAS_CLASS]->(c)
        """,
        root_id=ROOT_THING_ID,
        **row,
    ).consume()


def upsert_subject(session: Session, row: dict[str, Any]) -> None:
    session.run(
        """
        MERGE (c:Class {class_id: $class_id})
        MERGE (s:Subject {subject_id: $subject_id})
        SET s.subject_name = $subject_name,
            s.subject_type = $subject_type,
            s.mongo_id = $mongo_id,
            s.import_key = $import_key,
            s.updated_at = datetime()
        WITH c, s
        OPTIONAL MATCH (old:Class)-[r:HAS_SUBJECT]->(s)
        WHERE old.class_id <> $class_id
        DELETE r
        MERGE (c)-[:HAS_SUBJECT]->(s)
        """,
        **row,
    ).consume()


def upsert_topic(session: Session, row: dict[str, Any]) -> None:
    session.run(
        """
        MERGE (s:Subject {subject_id: $subject_id})
        MERGE (t:Topic {topic_id: $topic_id})
        SET t.topic_name = $topic_name,
            t.topic_num = $topic_num,
            t.mongo_id = $mongo_id,
            t.import_key = $import_key,
            t.embedding_text = CASE WHEN $embedding_text IS NULL THEN t.embedding_text ELSE $embedding_text END,
            t.embedding_model = CASE WHEN $embedding_model IS NULL THEN t.embedding_model ELSE $embedding_model END,
            t.embedding = CASE WHEN $embedding IS NULL THEN t.embedding ELSE $embedding END,
            t.updated_at = datetime()
        WITH s, t
        OPTIONAL MATCH (old:Subject)-[r:HAS_TOPIC]->(t)
        WHERE old.subject_id <> $subject_id
        DELETE r
        MERGE (s)-[:HAS_TOPIC]->(t)
        """,
        **row,
    ).consume()


def upsert_lesson(session: Session, row: dict[str, Any]) -> None:
    session.run(
        """
        MERGE (t:Topic {topic_id: $topic_id})
        MERGE (l:Lesson {lesson_id: $lesson_id})
        SET l.lesson_name = $lesson_name,
            l.lesson_num = $lesson_num,
            l.lesson_type = $lesson_type,
            l.mongo_id = $mongo_id,
            l.import_key = $import_key,
            l.updated_at = datetime()
        WITH t, l
        OPTIONAL MATCH (old:Topic)-[r:HAS_LESSON]->(l)
        WHERE old.topic_id <> $topic_id
        DELETE r
        MERGE (t)-[:HAS_LESSON]->(l)
        """,
        **row,
    ).consume()


def upsert_chunk(session: Session, row: dict[str, Any]) -> None:
    session.run(
        """
        MERGE (l:Lesson {lesson_id: $lesson_id})
        MERGE (c:Chunk {chunk_id: $chunk_id})
        SET c.chunk_name = $chunk_name,
            c.chunk_num = $chunk_num,
            c.mongo_id = $mongo_id,
            c.import_key = $import_key,
            c.updated_at = datetime()
        WITH l, c
        OPTIONAL MATCH (old:Lesson)-[r:HAS_CHUNK]->(c)
        WHERE old.lesson_id <> $lesson_id
        DELETE r
        MERGE (l)-[:HAS_CHUNK]->(c)
        """,
        **row,
    ).consume()


def upsert_chunk_keyword(session: Session, row: dict[str, Any]) -> None:
    session.run(
        """
        MERGE (c:Chunk {chunk_id: $chunk_id})
        MERGE (kw:Keyword {keyword_key: $keyword_key})
        SET kw.keyword_id = $keyword_id,
            kw.keyword_name = $keyword_name,
            kw.keyword_slug = $keyword_slug,
            kw.chunk_id = $chunk_id,
            kw.mongo_id = $mongo_id,
            kw.updated_at = datetime()
        MERGE (c)-[:HAS_KEYWORD]->(kw)
        """,
        **row,
    ).consume()
