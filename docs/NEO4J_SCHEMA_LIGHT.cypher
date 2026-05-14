
// Notes:
//   - Neo syncable Mongo cols: class, subject, topic, lesson, chunk, chunk_keyword.
//   - chunk_keyword is routed to Neo label Keyword, not a relationship table node.
//   - Root node is (Thing {id: 'thing', name: 'Thing'}).
//   - Topic embedding vector index is named topic_embedding_idx on (:Topic).embedding.
//   - Keyword node key is keyword_key. For chunk keywords, sync_service builds keyword_key as
//     '{chunk_pg_id}::{keyword_name}'. Standalone keyword sync may use keyword_id.

// -----------------------------------------------------------------------------
// Uniqueness constraints based on MERGE keys in neo_sync_service.py
// -----------------------------------------------------------------------------

CREATE CONSTRAINT thing_id_unique IF NOT EXISTS
FOR (t:Thing)
REQUIRE t.id IS UNIQUE;

CREATE CONSTRAINT class_id_unique IF NOT EXISTS
FOR (c:Class)
REQUIRE c.class_id IS UNIQUE;

CREATE CONSTRAINT subject_id_unique IF NOT EXISTS
FOR (s:Subject)
REQUIRE s.subject_id IS UNIQUE;

CREATE CONSTRAINT topic_id_unique IF NOT EXISTS
FOR (t:Topic)
REQUIRE t.topic_id IS UNIQUE;

CREATE CONSTRAINT lesson_id_unique IF NOT EXISTS
FOR (l:Lesson)
REQUIRE l.lesson_id IS UNIQUE;

CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
FOR (c:Chunk)
REQUIRE c.chunk_id IS UNIQUE;

CREATE CONSTRAINT keyword_key_unique IF NOT EXISTS
FOR (k:Keyword)
REQUIRE k.keyword_key IS UNIQUE;

// -----------------------------------------------------------------------------
// Vector index defined in neo_sync_service.py and queried in neo_search_service.py
// -----------------------------------------------------------------------------

CREATE VECTOR INDEX topic_embedding_idx IF NOT EXISTS
FOR (n:Topic) ON (n.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

// -----------------------------------------------------------------------------
// Node labels and properties written/read by code
// -----------------------------------------------------------------------------

// (:Thing)
//   id: string                  // set to 'thing' by _ensure_thing()
//   name: string                // set to 'Thing'

// (:Class)
//   class_id: string            // PostgreSQL class.class_id
//   class_name: string

// (:Subject)
//   subject_id: string          // PostgreSQL subject.subject_id
//   subject_name: string

// (:Topic)
//   topic_id: string            // PostgreSQL topic.topic_id
//   topic_name: string
//   topic_num: integer|null
//   embedding: list<float>|null // 768-dim vector; set only when keyword text exists

// (:Lesson)
//   lesson_id: string           // PostgreSQL lesson.lesson_id
//   lesson_name: string
//   lesson_num: integer|null

// (:Chunk)
//   chunk_id: string            // PostgreSQL chunk.chunk_id
//   chunk_name: string
//   chunk_num: integer|null

// (:Keyword)
//   keyword_key: string         // for chunk keyword: '{chunk_pg_id}::{keyword_name}'
//   keyword_name: string
//   chunk_id: string|null       // set/kept by _upsert_keyword(); usually chunk PG id

// -----------------------------------------------------------------------------
// Relationship topology written/read by code
// -----------------------------------------------------------------------------

// (Thing)-[:HAS_CLASS]->(Class)
// (Class)-[:HAS_SUBJECT]->(Subject)
// (Subject)-[:HAS_TOPIC]->(Topic)
// (Topic)-[:HAS_LESSON]->(Lesson)
// (Lesson)-[:HAS_CHUNK]->(Chunk)
// (Chunk)-[:HAS_KEYWORD]->(Keyword)

// Ensure the root node expected by _ensure_thing() and admin relation queries.
MERGE (root:Thing {id: 'thing'})
ON CREATE SET root.name = 'Thing';


