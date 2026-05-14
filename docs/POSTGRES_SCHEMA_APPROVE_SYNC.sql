-- =========================================================
-- APPROVE-TO-SYNC POSTGRESQL SCHEMA
-- Rule:
--   - PostgreSQL stores the relational learning hierarchy only.
--   - Asset metadata stays in MongoDB; PDF/binary files stay in MinIO.
--   - Table names are singular.
--   - Ordinal fields use *_num, especially chunk_num.
-- =========================================================

-- =========================================================
-- 1. CLASS
-- =========================================================
CREATE TABLE IF NOT EXISTS "class" (
    class_id VARCHAR PRIMARY KEY,
    class_name TEXT NOT NULL,
    mongo_id VARCHAR(24) UNIQUE NOT NULL,
    import_key TEXT UNIQUE,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================
-- 2. SUBJECT
-- =========================================================
CREATE TABLE IF NOT EXISTS subject (
    subject_id VARCHAR PRIMARY KEY,
    subject_name TEXT NOT NULL,
    subject_category VARCHAR(16) NOT NULL DEFAULT 'document',
    subject_type VARCHAR,
    bucket_name VARCHAR,
    mongo_id VARCHAR(24) UNIQUE NOT NULL,
    import_key TEXT UNIQUE,

    class_id VARCHAR NOT NULL,

    CONSTRAINT fk_subject_class
        FOREIGN KEY (class_id)
        REFERENCES "class"(class_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_subject_category
        CHECK (subject_category IN ('document', 'image', 'video')),

    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================
-- 3. TOPIC
-- =========================================================
CREATE TABLE IF NOT EXISTS topic (
    topic_id VARCHAR PRIMARY KEY,
    topic_name TEXT NOT NULL,
    topic_category VARCHAR(16) NOT NULL DEFAULT 'document',
    topic_num INTEGER NOT NULL,
    mongo_id VARCHAR(24) UNIQUE NOT NULL,
    import_key TEXT UNIQUE,

    subject_id VARCHAR NOT NULL,

    CONSTRAINT fk_topic_subject
        FOREIGN KEY (subject_id)
        REFERENCES subject(subject_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_topic_category
        CHECK (topic_category IN ('document', 'image', 'video')),

    CONSTRAINT uq_topic_num_per_subject
        UNIQUE (subject_id, topic_num),

    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================
-- 4. LESSON
-- =========================================================
CREATE TABLE IF NOT EXISTS lesson (
    lesson_id VARCHAR PRIMARY KEY,
    lesson_name TEXT NOT NULL,
    lesson_category VARCHAR(16) NOT NULL DEFAULT 'document',
    lesson_type VARCHAR(32),
    lesson_num INTEGER NOT NULL,
    mongo_id VARCHAR(24) UNIQUE NOT NULL,
    import_key TEXT UNIQUE,

    topic_id VARCHAR NOT NULL,

    CONSTRAINT fk_lesson_topic
        FOREIGN KEY (topic_id)
        REFERENCES topic(topic_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_lesson_category
        CHECK (lesson_category IN ('document', 'image', 'video')),

    CONSTRAINT uq_lesson_num_per_topic
        UNIQUE (topic_id, lesson_num),

    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================
-- 5. CHUNK
-- =========================================================
CREATE TABLE IF NOT EXISTS "chunk" (
    chunk_id VARCHAR PRIMARY KEY,
    chunk_name TEXT NOT NULL,
    chunk_category VARCHAR(16) NOT NULL DEFAULT 'document',
    chunk_type VARCHAR(32),
    chunk_num INTEGER NOT NULL,
    mongo_id VARCHAR(24) UNIQUE NOT NULL,
    import_key TEXT UNIQUE,

    lesson_id VARCHAR NOT NULL,

    CONSTRAINT fk_chunk_lesson
        FOREIGN KEY (lesson_id)
        REFERENCES lesson(lesson_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_chunk_category
        CHECK (chunk_category IN ('document', 'image', 'video')),

    CONSTRAINT uq_chunk_num_per_lesson
        UNIQUE (lesson_id, chunk_num),

    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================
-- 6. KEYWORD
-- Core keyword table; chunk-specific keyword links can be represented by
-- keyword.chunk_id and/or chunk_keyword.
-- =========================================================
CREATE TABLE IF NOT EXISTS keyword (
    keyword_id VARCHAR(96) PRIMARY KEY,
    keyword_name TEXT NOT NULL,
    keyword_slug VARCHAR UNIQUE NOT NULL,
    keyword_embedding JSONB,
    embedding_provider TEXT,
    aliases JSONB,
    mongo_id VARCHAR(24) UNIQUE NOT NULL,
    import_key TEXT UNIQUE,

    chunk_id VARCHAR,

    CONSTRAINT fk_keyword_chunk
        FOREIGN KEY (chunk_id)
        REFERENCES "chunk"(chunk_id)
        ON DELETE CASCADE,

    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Optional normalized keyword alias table used by current keyword import code.
CREATE TABLE IF NOT EXISTS keyword_alias (
    alias_id VARCHAR PRIMARY KEY,
    keyword_id VARCHAR NOT NULL REFERENCES keyword(keyword_id) ON DELETE CASCADE,
    keyword_name TEXT,
    alias_name TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    mongo_id VARCHAR(24) UNIQUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Optional many-to-many link table used by current keyword import code.
CREATE TABLE IF NOT EXISTS chunk_keyword (
    chunk_id VARCHAR NOT NULL REFERENCES "chunk"(chunk_id) ON DELETE CASCADE,
    keyword_id VARCHAR NOT NULL REFERENCES keyword(keyword_id) ON DELETE CASCADE,
    mongo_id VARCHAR(24) UNIQUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chunk_id, keyword_id)
);

-- Topic keyword bag and topic embedding are kept in PostgreSQL because Neo4j
-- uses topic.embedding for vector search.
CREATE TABLE IF NOT EXISTS topic_bag (
    topic_bag_id VARCHAR PRIMARY KEY,
    topic_id VARCHAR NOT NULL UNIQUE REFERENCES topic(topic_id) ON DELETE CASCADE,
    topic_name TEXT,
    keyword_refs JSONB,
    total_keywords INTEGER NOT NULL DEFAULT 0,
    keyword_embedding_text TEXT,
    mongo_id VARCHAR(24) UNIQUE,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS topic_embedding (
    topic_id VARCHAR PRIMARY KEY REFERENCES topic(topic_id) ON DELETE CASCADE,
    embedding JSONB NOT NULL,
    embedding_text TEXT,
    model_name VARCHAR NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS import_job (
    import_job_id VARCHAR PRIMARY KEY,
    job_id VARCHAR,
    book_stem VARCHAR,
    bundle_path TEXT,
    status VARCHAR,
    schema_name VARCHAR,
    upload_minio BOOLEAN,
    counts JSONB,
    errors JSONB,
    mongo_id VARCHAR(24) UNIQUE,
    import_key TEXT UNIQUE,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sync_run (
    sync_run_id VARCHAR PRIMARY KEY,
    job_id VARCHAR,
    target VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    counts JSONB,
    errors JSONB,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pg_topic_subject_num ON topic(subject_id, topic_num);
CREATE INDEX IF NOT EXISTS idx_pg_lesson_topic_num ON lesson(topic_id, lesson_num);
CREATE INDEX IF NOT EXISTS idx_pg_chunk_lesson_num ON "chunk"(lesson_id, chunk_num);
CREATE INDEX IF NOT EXISTS idx_pg_keyword_chunk ON keyword(chunk_id);

-- If an older development database already has the old PostgreSQL asset table
-- and you want to remove it after confirming assets are safely in MongoDB:
-- DROP TABLE IF EXISTS asset CASCADE;
