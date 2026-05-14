# Approve-to-sync flow

The backend follows this rule:

```text
Approve topic  -> MongoDB topic + MongoDB asset + MinIO topic PDF + PostgreSQL/Neo4j sync
Approve lesson -> MongoDB lesson + MongoDB asset + MinIO lesson PDF + PostgreSQL/Neo4j sync
Approve chunk  -> MongoDB chunk + MongoDB asset + MinIO chunk PDF + PostgreSQL/Neo4j sync
```

## Storage responsibility

```text
MongoDB      : metadata source of truth, including asset documents
MinIO        : PDF/binary object storage
PostgreSQL   : relational learning hierarchy only; no asset table
Neo4j        : light graph nodes and relationships only
```

## Naming convention

The backend uses singular table/collection names:

```text
class, subject, topic, lesson, chunk, keyword
```

It also uses:

```text
topic_num, lesson_num, chunk_num
```

not:

```text
topic_number, lesson_number, chunk_number
```

## PostgreSQL rule

PostgreSQL does not store asset rows. Asset metadata remains in MongoDB collection `asset`, and actual files remain in MinIO.

Core PostgreSQL relation:

```text
class -> subject -> topic -> lesson -> chunk -> keyword
```

## Neo4j rule

Neo4j uses light nodes only:

```text
(Thing)-[:HAS_CLASS]->(Class)
(Class)-[:HAS_SUBJECT]->(Subject)
(Subject)-[:HAS_TOPIC]->(Topic)
(Topic)-[:HAS_LESSON]->(Lesson)
(Lesson)-[:HAS_CHUNK]->(Chunk)
(Chunk)-[:HAS_KEYWORD]->(Keyword)
```

Neo4j does not store MongoDB ids, import keys, category fields, MinIO object keys, or full asset metadata.

## Sync behavior

Topic and lesson approvals save MongoDB metadata and assets first, then call sync. Chunk approval saves the chunk metadata and MongoDB asset, then selectively syncs that chunk path to PostgreSQL and Neo4j.

If sync fails, MongoDB metadata and MinIO assets remain saved. The approval payload records sync errors so `/api/sync/metadata` can be retried.
