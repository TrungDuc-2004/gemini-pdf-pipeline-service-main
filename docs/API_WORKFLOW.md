# API Workflow

Base URL:

```text
http://localhost:8100
```

The recommended flow is manual review-first. Start each extraction stage, poll status, inspect/edit the result, then approve before moving to the next stage.

## Create Job

```bash
curl -X POST http://localhost:8100/api/jobs \
  -F "file=@/absolute/path/to/book.pdf;type=application/pdf" \
  -F "book_name=Tin học 11 Kết nối tri thức" \
  -F "class_name=11" \
  -F "subject_name=Tin học" \
  -F "subject_type=Kết nối tri thức" \
  -F "pipeline_mode=review_first" \
  -F "enable_kaggle=false" \
  -F "enable_keywords=true"
```

## Status and Logs

```bash
curl http://localhost:8100/api/jobs/{job_id}/status
curl "http://localhost:8100/api/jobs/{job_id}/logs?lines=200"
```

## Topics

Start extraction:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/extract/topics
```

Poll until `reviewing_topics` or `error`:

```bash
curl http://localhost:8100/api/jobs/{job_id}/status
```

Get topics:

```bash
curl http://localhost:8100/api/jobs/{job_id}/topics
```

Save edited topics:

```bash
curl -X PUT http://localhost:8100/api/jobs/{job_id}/topics \
  -H "Content-Type: application/json" \
  -d '{"topics":[{"topic_num":"1","topic_name":"...","start":6,"end":32}]}'
```

Approve:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/topics/approve
```

## Lessons

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/extract/lessons
curl http://localhost:8100/api/jobs/{job_id}/status
curl http://localhost:8100/api/jobs/{job_id}/lessons
```

Save edited lessons:

```bash
curl -X PUT http://localhost:8100/api/jobs/{job_id}/lessons \
  -H "Content-Type: application/json" \
  -d '{"lessons":[{"lesson_num":"1","lesson_name":"...","topic_num":"1","start":6,"end":10}]}'
```

Approve:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/lessons/approve
```

## Chunks

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/extract/chunks
curl http://localhost:8100/api/jobs/{job_id}/status
curl http://localhost:8100/api/jobs/{job_id}/chunks
```

Save edited chunks:

```bash
curl -X PUT http://localhost:8100/api/jobs/{job_id}/chunks \
  -H "Content-Type: application/json" \
  -d '{"chunks":[{"lesson_stem":"book_lesson_01","chunk_num":"1","start":1,"end":3,"title":"..."}]}'
```

Approve:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/chunks/approve
```

Manual chunk utilities:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/chunks/add
curl -X DELETE http://localhost:8100/api/jobs/{job_id}/chunks/{chunk_id}
curl -X POST http://localhost:8100/api/jobs/{job_id}/chunks/recut
```

## Prepare Bundle

Prepare normally:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/prepare-bundle
```

Skip Kaggle for one run, even if the job was created with `enable_kaggle=true`:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?skip_kaggle=true"
```

Recovery/test mode without Gemini keyword calls:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?skip_keywords=true"
```

Safe recovery mode without Kaggle or Gemini keyword calls:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?skip_kaggle=true&skip_keywords=true"
```

Retry missing/empty/error keyword files:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?retry_failed_keywords_only=true"
```

Poll until `bundle_ready` or `error`:

```bash
curl http://localhost:8100/api/jobs/{job_id}/status
```

Get bundle summary:

```bash
curl http://localhost:8100/api/jobs/{job_id}/bundle
```

The bundle summary includes Kaggle status when available:

```json
{
  "kaggle": {
    "enabled": true,
    "skipped": false,
    "status": "completed",
    "request_id": "...",
    "attempts": 1,
    "output_zip": "...",
    "applied": true,
    "failure_reason": null
  }
}
```

Download ZIP:

```bash
curl -L http://localhost:8100/api/jobs/{job_id}/bundle/download \
  -o {book_stem}_bundle.zip
```

## MongoDB Import

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/import-mongodb
curl http://localhost:8100/api/jobs/{job_id}/mongo-import-result
```

By default this endpoint uploads PDFs to MinIO and writes Metadata-Edu shaped documents to `MONGO_DB_NAME`:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/import-mongodb?upload_minio=true&dry_run=false"
```

Useful variants:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/import-mongodb?upload_minio=false"
curl -X POST "http://localhost:8100/api/jobs/{job_id}/import-mongodb?dry_run=true"
```

Collections written:

```text
class
subject
topic
lesson
chunk
asset
keyword
keyword_alias
chunk_keyword
topic_bag
import_job
```

MinIO paths use:

```text
documents/lop-{grade}/tin-hoc/subject/
documents/lop-{grade}/tin-hoc/topic/topic_{NN}/
documents/lop-{grade}/tin-hoc/lesson/topic_{NN}-lesson_{NN}/
documents/lop-{grade}/tin-hoc/chunk/topic_{NN}-lesson_{NN}-chunk_{NN}/
```

Keyword files that are empty, placeholder, invalid, or contain `error` are skipped. Topic, lesson, chunk, and asset import still succeeds.

## Fake Backend Examples

Fake backend base URL:

```text
http://localhost:8200
```

Smoke checks:

```bash
curl http://localhost:8200/health
curl http://localhost:8200/demo/jobs/{job_id}/status
curl http://localhost:8200/demo/jobs/{job_id}/bundle
curl http://localhost:8200/demo/jobs/{job_id}/mongo-import-result
```

Forwarded manual stages:

```bash
curl -X POST http://localhost:8200/demo/jobs/{job_id}/extract-topics
curl -X POST http://localhost:8200/demo/jobs/{job_id}/approve-topics
curl -X POST http://localhost:8200/demo/jobs/{job_id}/extract-lessons
curl -X POST http://localhost:8200/demo/jobs/{job_id}/approve-lessons
curl -X POST http://localhost:8200/demo/jobs/{job_id}/extract-chunks
curl -X POST http://localhost:8200/demo/jobs/{job_id}/approve-chunks
curl -X POST "http://localhost:8200/demo/jobs/{job_id}/prepare-bundle?skip_kaggle=true&skip_keywords=true"
```

No-review demo helper:

```bash
curl -X POST "http://localhost:8200/demo/jobs/{job_id}/run-until-bundle-ready-no-review?skip_keywords=true"
```

This helper is for integration testing only. It is not the real `full_auto` mode.

## Long-Running Task Notes

Extraction and keyword tasks can take minutes. Current implementation uses FastAPI background tasks and disk progress files. For production use, move these workloads to a worker queue and keep FastAPI focused on request/response and polling.
