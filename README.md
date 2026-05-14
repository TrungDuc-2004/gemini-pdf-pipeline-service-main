# gemini-pdf-pipeline-service

Standalone FastAPI service for a review-first PDF processing pipeline based on the old `gemini_pipeline` workflow.

The service is being built phase by phase so other backend projects can reuse PDF upload, extraction review, final bundle generation, and test import APIs without depending on the old backend.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API Workflow](docs/API_WORKFLOW.md)
- [Development](docs/DEVELOPMENT.md)
- [Phase Summary](docs/PHASE_SUMMARY.md)

## Why review_first

The primary workflow is `review_first` because Gemini extraction needs human review between stages:

```text
PDF upload
  -> extract topics
  -> review/edit/approve topics
  -> extract lessons
  -> review/edit/approve lessons
  -> extract chunks
  -> review/edit/approve chunks
  -> prepare final bundle
```

This makes each extraction stage debuggable before data is imported anywhere.

## Current Scope

Phase 1 implemented:

- project skeleton
- `GET /health`
- PDF job upload with disk state
- job status API
- job log tail API
- initial endpoint shape for later phases

Phase 2 adds local Gemini key management and persisted rotation state.

Phase 3 adds topic extraction using Gemini PDF prompting and local key rotation.

Phase 4 adds a simple React/Vite Topic Review UI in `frontend/review-ui`.

Phase 5 adds lesson rebuild/extraction after topics are approved.

Phase 6 adds Lesson Review UI to `frontend/review-ui`.

Phase 7 adds backend chunk extraction after lessons are approved.

Phase 8 adds Chunk Review UI to `frontend/review-ui`.

Phase 9 adds final bundle preparation and optional Gemini keyword extraction.

Phase 10 adds a standalone MongoDB import adapter for testing final bundles.

Phase 11 adds a fake backend integration app that calls the pipeline service over HTTP.

Phase 12 adds optional Kaggle OCR/cutline post-processing during prepare-bundle.

Phase 13 maps prepared bundles to the Metadata-Edu MongoDB schema and uploads generated PDFs to MinIO.

This project still does not implement PostgreSQL, Neo4j, production worker queues, or `full_auto`.

## Current Verified Status

Verified real job:

```text
7f243448-4e57-4133-9137-f7a87c5030fc
```

Verified real PDF:

```text
Tin-hoc-11-ket-noi-tri-thuc.pdf
```

Verified pipeline counts:

```text
topics=7
lessons=31
chunks=70
topic_pdfs=7
lesson_pdfs=31
chunk_pdfs=70
```

Verified MongoDB counts:

```text
class=1
subject=1
topic=7
lesson=31
chunk=70
keyword=5
chunk_keyword=5
```

Keyword extraction is intentionally recoverable. If Gemini keys are cooling down, run prepare-bundle with `skip_keywords=true`; MongoDB import skips empty, placeholder, invalid, and error keyword files.

## Quick Start

Backend:

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

Frontend:

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service/frontend/review-ui
npm install
cp .env.example .env
npm run dev
```

Fake backend:

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service/fake_backend
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8200 --reload
```

Open:

```text
Pipeline API: http://localhost:8100/docs
Review UI:    http://localhost:5173
Fake backend: http://localhost:8200/docs
```

## Secret Safety

Do not commit real Gemini API keys, MongoDB credentials, or any populated local `.env` file. Use placeholders such as:

```env
GEMINI_API_KEYS=your_key_1,your_key_2
```

If a real key was pasted into chat, committed, or otherwise exposed, rotate or revoke it before continuing.

## Install

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Gemini Key Config

Configure Gemini keys in `.env` using either a comma-separated list:

```env
GEMINI_MODEL=gemini-2.5-flash
GEMINI_API_KEYS=key1,key2,key3
```

Or numbered variables:

```env
GEMINI_MODEL=gemini-2.5-flash
GEMINI_API_KEY_1=key1
GEMINI_API_KEY_2=key2
GEMINI_API_KEY_3=key3
```

Default model:

```text
gemini-2.5-flash
```

Do not commit real Gemini keys. `.env` is ignored by git.

For real topic extraction, configure at least one real Gemini API key before starting the service.

## Gemini Rotation State

The key manager persists local rotation state at:

```text
workspace/gemini_rotation_state.json
```

The state tracks:

- `current_index`
- key cooldowns
- dead keys
- last errors
- `updated_at`

Actual key strings are never returned by debug APIs.

### Prune dead Gemini keys

Use this maintenance flow when `/api/debug/gemini-keys` shows permanently dead keys.
Only keys with debug `status=dead` are removed; temporary 429 quota/rate-limit keys are kept.
The script backs up `.env`, rewrites Gemini key entries, and backs up/removes
`workspace/gemini_rotation_state.json` because key indexes change after pruning.

```bash
curl http://localhost:8100/api/debug/gemini-keys
python3 tools/prune_dead_gemini_keys.py --dry-run
python3 tools/prune_dead_gemini_keys.py
```

Restart the backend after pruning, then verify:

```bash
curl http://localhost:8100/api/debug/gemini-keys
```

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

Swagger UI:

```text
http://localhost:8100/docs
```

## MongoDB Config

MongoDB import writes to the Metadata-Edu shaped database used by the thesis demo.

Recommended local development target:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=data-ai-tra-cuu
MINIO_ENDPOINT=http://127.0.0.1:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ai-tra-cuu
MINIO_SECURE=false
MINIO_PUBLIC_URL=http://127.0.0.1:9000
```

Earlier verification used `gemini_pipeline_test`; that database is not deleted or migrated automatically. MongoDB creates `data-ai-tra-cuu` automatically on the first successful insert.

After changing `.env`, restart the FastAPI service so settings are reloaded:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

Run MongoDB locally with your normal installation, for example:

```bash
brew services start mongodb-community
```

or:

```bash
mongod --dbpath /path/to/db
```

## Optional Kaggle Config

Kaggle OCR/cutline is optional. It runs after chunks are reviewed during `prepare-bundle`, before keyword extraction. It is not the initial chunk extraction step.

Configure only when you want to create jobs with `enable_kaggle=true`:

```env
ENABLE_KAGGLE=false
KAGGLE_USERNAME=
KAGGLE_KEY=
KAGGLE_KERNEL_REF=dat261303/debug-cutlines-auto
KAGGLE_DATASET_ID=dat261303/kaggle-pack
KAGGLE_MAX_ATTEMPTS=3
KAGGLE_POLL_SECONDS=20
```

Do not commit Kaggle credentials. The service also supports the standard `~/.kaggle/kaggle.json` credential file.

## Run Topic Review UI

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service/frontend/review-ui
npm install
cp .env.example .env
npm run dev
```

Open:

```text
http://localhost:5173
```

The frontend uses:

```env
VITE_API_BASE_URL=http://localhost:8100
```

The backend allows development CORS from `localhost:5173` and `127.0.0.1:5173`.

Frontend review flow:

```text
create job
  -> extract topics
  -> approve topics
  -> extract lessons
  -> review/save/approve lessons
  -> extract chunks
  -> review/save/approve chunks
  -> prepare final bundle
```

## Test Health

```bash
curl http://localhost:8100/health
```

Expected:

```json
{
  "status": "ok",
  "service": "gemini-pdf-pipeline-service"
}
```

## Create a Job

Use Swagger at `http://localhost:8100/docs`, or use curl:

```bash
curl -X POST http://localhost:8100/api/jobs \
  -F "file=@/absolute/path/to/book.pdf;type=application/pdf" \
  -F "book_name=Tin hoc 11" \
  -F "class_name=11" \
  -F "subject_name=Tin hoc" \
  -F "subject_type=schoolbook" \
  -F "pipeline_mode=review_first" \
  -F "enable_kaggle=false" \
  -F "enable_keywords=true"
```

The service creates:

```text
workspace/{job_id}/source.pdf
workspace/{job_id}/logs/job.log
workspace/{job_id}/job_config.json
workspace/{job_id}/job_state.json
workspace/{job_id}/progress.json
workspace/{job_id}/result.json
```

## Job APIs

```text
GET /api/jobs/{job_id}
GET /api/jobs/{job_id}/status
GET /api/jobs/{job_id}/logs?lines=100
```

## Gemini Debug APIs

These endpoints test key loading and rotation state without calling Gemini:

```text
GET  /api/debug/gemini-keys
POST /api/debug/gemini-keys/rotate
POST /api/debug/gemini-keys/mark-current-cooldown
POST /api/debug/gemini-keys/mark-current-dead
POST /api/debug/gemini-keys/clear-state
```

Example:

```bash
curl http://localhost:8100/api/debug/gemini-keys

curl -X POST http://localhost:8100/api/debug/gemini-keys/rotate

curl -X POST http://localhost:8100/api/debug/gemini-keys/mark-current-cooldown \
  -H "Content-Type: application/json" \
  -d '{"error":"manual test","cooldown_seconds":60}'

curl -X POST http://localhost:8100/api/debug/gemini-keys/mark-current-dead \
  -H "Content-Type: application/json" \
  -d '{"error":"manual test"}'

curl -X POST http://localhost:8100/api/debug/gemini-keys/clear-state
```

Safe status response shape:

```json
{
  "ok": true,
  "key_count": 3,
  "current_index": 0,
  "model": "gemini-2.5-flash",
  "state_path": "/path/to/workspace/gemini_rotation_state.json",
  "keys": [
    {
      "index": 0,
      "status": "available",
      "cooldown_until": null,
      "dead_reason": null,
      "last_error": null
    }
  ]
}
```

## Topic Extraction

Phase 3 implements the first extraction stage:

```text
PDF upload
  -> POST /api/jobs/{job_id}/extract/topics
  -> poll /api/jobs/{job_id}/status
  -> GET /api/jobs/{job_id}/topics
  -> PUT /api/jobs/{job_id}/topics
  -> POST /api/jobs/{job_id}/topics/approve
```

Start topic extraction:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/extract/topics
```

Poll status:

```bash
curl http://localhost:8100/api/jobs/{job_id}/status
```

Read extracted topics:

```bash
curl http://localhost:8100/api/jobs/{job_id}/topics
```

Save edited topics without approving:

```bash
curl -X PUT http://localhost:8100/api/jobs/{job_id}/topics \
  -H "Content-Type: application/json" \
  -d '{
    "topics": [
      {
        "topic_num": "1",
        "topic_name": "MÁY TÍNH VÀ XÃ HỘI TRI THỨC",
        "start": 7,
        "end": 18,
        "raw_heading": "Chủ đề 1.",
        "raw_title": "MÁY TÍNH VÀ XÃ HỘI TRI THỨC"
      }
    ]
  }'
```

Approve current topics from `topics_partial.json`:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/topics/approve
```

Or approve topics from request body:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/topics/approve \
  -H "Content-Type: application/json" \
  -d '{"topics":[{"topic_num":"1","topic_name":"...","start":7,"end":18}]}'
```

Expected topic-stage artifacts:

```text
workspace/{job_id}/topics_partial.json
workspace/{job_id}/approved_topics.json        # after approval
workspace/{job_id}/extraction_state.json
workspace/{job_id}/progress.json
workspace/{job_id}/result.json
workspace/{job_id}/logs/topics.log
workspace/{job_id}/{book_stem}/{book_stem}.json
workspace/{job_id}/{book_stem}/Topic/topic_XX/*.pdf
workspace/{job_id}/{book_stem}/Topic/topic_XX/*.json
workspace/{job_id}/{book_stem}/Lesson/lesson_XX/*.pdf
workspace/{job_id}/{book_stem}/Lesson/lesson_XX/*.json
```

Topic extraction also creates lesson PDFs and raw lesson metadata because the old topic prompt returns both topics and lessons. The later lesson stage uses approved topics and this raw lesson state to rebuild canonical lesson artifacts.

If no Gemini keys are configured, extraction fails cleanly with job status `error` and writes the error to `result.json`, `progress.json`, `logs/topics.log`, and `logs/job.log`.

## Lesson Extraction

Phase 5 implements the next review-first stage after topics are approved:

```text
approved topics
  -> POST /api/jobs/{job_id}/extract/lessons
  -> poll /api/jobs/{job_id}/status
  -> GET /api/jobs/{job_id}/lessons
  -> PUT /api/jobs/{job_id}/lessons
  -> POST /api/jobs/{job_id}/lessons/approve
```

Start lesson extraction/rebuild:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/extract/lessons
```

Poll status:

```bash
curl http://localhost:8100/api/jobs/{job_id}/status
```

Read extracted lessons:

```bash
curl http://localhost:8100/api/jobs/{job_id}/lessons
```

Save edited lessons without approving:

```bash
curl -X PUT http://localhost:8100/api/jobs/{job_id}/lessons \
  -H "Content-Type: application/json" \
  -d '{
    "lessons": [
      {
        "lesson_num": "1",
        "lesson_name": "MỘT SỐ KHÁI NIỆM",
        "topic_num": "1",
        "topic_name": "MÁY TÍNH VÀ XÃ HỘI TRI THỨC",
        "start": 6,
        "end": 10,
        "raw_heading": "Bài 1.",
        "raw_title": "MỘT SỐ KHÁI NIỆM"
      }
    ]
  }'
```

Approve current lessons from `lessons_partial.json`:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/lessons/approve
```

Expected lesson-stage artifacts:

```text
workspace/{job_id}/lessons_partial.json
workspace/{job_id}/approved_lessons.json        # after approval
workspace/{job_id}/progress.json
workspace/{job_id}/result.json
workspace/{job_id}/logs/lessons.log
workspace/{job_id}/{book_stem}/{book_stem}.json
workspace/{job_id}/{book_stem}/Topic/topic_XX/*.pdf
workspace/{job_id}/{book_stem}/Topic/topic_XX/*.json
workspace/{job_id}/{book_stem}/Lesson/lesson_XX/*.pdf
workspace/{job_id}/{book_stem}/Lesson/lesson_XX/*.json
```

Lesson extraction in Phase 5 does not call Gemini. It uses `approved_topics.json` and `raw_lessons` from `extraction_state.json`, then rebuilds canonical `Topic/`, `Lesson/`, and the bundle manifest.

## Chunk Extraction

Phase 7 implements the backend chunk stage after lessons are approved:

```text
approved lessons
  -> POST /api/jobs/{job_id}/extract/chunks
  -> poll /api/jobs/{job_id}/status
  -> GET /api/jobs/{job_id}/chunks
  -> PUT /api/jobs/{job_id}/chunks
  -> POST /api/jobs/{job_id}/chunks/approve
```

Start chunk extraction:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/extract/chunks
```

Poll status:

```bash
curl http://localhost:8100/api/jobs/{job_id}/status
```

Read chunks:

```bash
curl http://localhost:8100/api/jobs/{job_id}/chunks
```

Save edited chunks:

```bash
curl -X PUT http://localhost:8100/api/jobs/{job_id}/chunks \
  -H "Content-Type: application/json" \
  -d '{"chunks":[{"lesson_stem":"book_lesson_01","chunk_num":"1","start":1,"end":3,"title":"..."}]}'
```

Approve current chunks from `chunks_partial.json`:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/chunks/approve
```

Manual chunk maintenance endpoints:

```text
POST   /api/jobs/{job_id}/chunks/add
DELETE /api/jobs/{job_id}/chunks/{chunk_id}
POST   /api/jobs/{job_id}/chunks/recut
```

Expected chunk-stage artifacts:

```text
workspace/{job_id}/chunks_partial.json
workspace/{job_id}/approved_chunks.json        # after approval
workspace/{job_id}/progress.json
workspace/{job_id}/result.json
workspace/{job_id}/logs/chunks.log
workspace/{job_id}/{book_stem}/{book_stem}.json
workspace/{job_id}/{book_stem}/Topic/
workspace/{job_id}/{book_stem}/Lesson/
workspace/{job_id}/{book_stem}/Chunk/{lesson_stem}/chunk_XX/*.pdf
workspace/{job_id}/{book_stem}/Chunk/{lesson_stem}/chunk_XX/*.json
workspace/{job_id}/{book_stem}/Chunk/{lesson_stem}/chunk_XX/*.keywords.json
```

Chunk extraction calls Gemini for each lesson PDF. It regenerates canonical `Topic/` and `Lesson/` before chunking so those artifacts remain available.

## Chunk Review UI

Phase 8 adds chunk review to the React UI:

```text
create job
  -> extract/approve topics
  -> extract/approve lessons
  -> extract/review/approve chunks
```

In the UI:

1. Load or create a job.
2. Click `Trích xuất chunk`.
3. Wait until status becomes `reviewing_chunks`.
4. Click `Tải danh sách chunk`.
5. Review chunks grouped by lesson.
6. Use `Lưu chunk` to save edits.
7. Use `Thêm chunk`, `Xóa chunk`, and `Cắt lại chunk` for manual corrections.
8. Click `Duyệt chunk` when the list is ready.

The chunk review screen reuses the existing status, log, and raw JSON panels for debugging.

## Prepare Bundle

Phase 9 prepares the final old-compatible bundle after chunks are approved:

```text
approved chunks
  -> POST /api/jobs/{job_id}/prepare-bundle
  -> poll /api/jobs/{job_id}/status
  -> GET /api/jobs/{job_id}/bundle
  -> GET /api/jobs/{job_id}/bundle/download
```

Start bundle preparation:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/prepare-bundle
```

Skip Kaggle for one run:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?skip_kaggle=true"
```

Recover or validate the final bundle without making Gemini keyword calls:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?skip_keywords=true"
```

Safe recovery mode without Kaggle or Gemini keyword calls:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?skip_kaggle=true&skip_keywords=true"
```

Retry only missing, empty, or errored keyword files while skipping successful keyword files:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?retry_failed_keywords_only=true"
```

Poll status:

```bash
curl http://localhost:8100/api/jobs/{job_id}/status
```

Successful completion sets status to:

```text
bundle_ready
```

Read bundle summary:

```bash
curl http://localhost:8100/api/jobs/{job_id}/bundle
```

Download ZIP:

```bash
curl -L http://localhost:8100/api/jobs/{job_id}/bundle/download \
  -o {book_stem}_bundle.zip
```

Final output format:

```text
output/{book_stem}/
  {book_stem}.json
  Topic/
  Lesson/
  Chunk/
```

Each chunk folder contains:

```text
*.pdf
*.json
*.keywords.json
```

Bundle preparation copies the reviewed workspace bundle into `output/{book_stem}` and rewrites internal JSON path fields from the workspace path to the output path.

### Kaggle OCR/Cutline

If the job was created with `enable_kaggle=true`, prepare-bundle sets status `running_kaggle`, builds `kaggle_pack/`, pushes the configured Kaggle dataset/kernel, downloads a request-specific ZIP, validates the ZIP top-level folder, and applies it back into `output/{book_stem}`.

Kaggle safety checks include:

- `expected_book_stem`
- per-run `request_id`
- `run_request.json`
- embedded request in the kernel script
- request-specific `current_run_status_{request_id}.json`
- stale dataset mismatch detection
- stale output artifact detection
- request-specific ZIP name
- ZIP top-level folder validation
- refusal to apply ZIP files for the wrong book

Kaggle result files:

```text
workspace/{job_id}/kaggle_result.json
workspace/{job_id}/logs/kaggle.log
output/_kaggle_outputs/{kernel_slug}/downloads/
```

### Keyword Extraction

If the job was created with `enable_keywords=true`, Phase 9 runs Gemini keyword extraction for each chunk PDF after copying the bundle. It writes or updates `.keywords.json` beside each chunk PDF and writes:

```text
workspace/{job_id}/keyword_summary.json
workspace/{job_id}/logs/keyword.log
workspace/{job_id}/logs/bundle.log
```

Keyword extraction is resumable: `.keywords.json` files with a non-empty `keywords` list are skipped, while missing, empty, placeholder, or errored files are retried. Existing keyword files in the output bundle are preserved when the workspace bundle is recopied.

If `enable_keywords=false` or `skip_keywords=true`, keyword extraction is skipped and placeholder `.keywords.json` files are created only when missing.

For now, a keyword failure fails the prepare-bundle stage clearly and sets job status to `error`.

## MongoDB Import

The import endpoint maps a prepared bundle into the Metadata-Edu schema and uploads generated PDFs to MinIO:

```text
bundle_ready
  -> POST /api/jobs/{job_id}/import-mongodb
  -> GET /api/jobs/{job_id}/mongo-import-result
```

Run import:

```bash
curl -X POST http://localhost:8100/api/jobs/{job_id}/import-mongodb
```

Optional query params:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/import-mongodb?upload_minio=true&dry_run=false"
curl -X POST "http://localhost:8100/api/jobs/{job_id}/import-mongodb?upload_minio=false"
curl -X POST "http://localhost:8100/api/jobs/{job_id}/import-mongodb?dry_run=true"
```

Read import result:

```bash
curl http://localhost:8100/api/jobs/{job_id}/mongo-import-result
```

Collections used:

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

Relationships use MongoDB `ObjectId` references:

```text
subject.class_id -> class._id
topic.subject_id -> subject._id
lesson.topic_id -> topic._id
chunk.lesson_id -> lesson._id
asset.owner_id -> subject/topic/lesson/chunk._id
chunk_keyword.chunk_id -> chunk._id
chunk_keyword.keyword_id -> keyword._id
topic_bag.topic_id -> topic._id
```

The importer is idempotent. It upserts class/subject/topic/lesson/chunk by stable `import_key`, assets by `object_key`, keywords by `keyword_slug`, chunk-keyword relations by `(chunk_id, keyword_id)`, and topic bags by `topic_id`.

MinIO object keys use the Metadata-Edu layout:

```text
documents/lop-{grade}/tin-hoc/subject/{book}.pdf
documents/lop-{grade}/tin-hoc/topic/topic_{NN}/{book}_topic_{NN}.pdf
documents/lop-{grade}/tin-hoc/lesson/topic_{NN}-lesson_{NN}/{book}_lesson_{NN}.pdf
documents/lop-{grade}/tin-hoc/chunk/topic_{NN}-lesson_{NN}-chunk_{NN}/{book}_lesson_{NN}_chunk_{NN}.pdf
```

Keyword behavior:

- Real non-empty keyword lists are imported.
- Missing, empty, placeholder, invalid, or errored `.keywords.json` files are skipped.
- Skipped keyword files are counted in the import result.
- Topic, lesson, and chunk import does not fail just because keywords are incomplete.

Verify in MongoDB Compass:

1. Connect to `mongodb://localhost:27017`.
2. Open database `data-ai-tra-cuu`.
3. Check collection counts for `class`, `subject`, `topic`, `lesson`, `chunk`, `keyword`, `chunk_keyword`, and `import_job`.

## Fake Backend Integration Test

Phase 11 adds `fake_backend/`, a minimal FastAPI app showing how another backend can call this standalone pipeline service through HTTP.

Run the pipeline service:

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

Run the fake backend:

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service/fake_backend
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8200 --reload
```

Smoke-test existing job integration:

```bash
curl http://localhost:8200/health
curl http://localhost:8200/demo/jobs/{job_id}/status
curl http://localhost:8200/demo/jobs/{job_id}/bundle
curl http://localhost:8200/demo/jobs/{job_id}/mongo-import-result
```

The fake backend also includes a no-review demo helper that approves current stage outputs as-is. It is only for integration testing and is not the real `full_auto` mode.

## Current API Coverage

The review-first workflow is implemented through MongoDB import:

```text
POST /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/status
GET  /api/jobs/{job_id}/logs

POST /api/jobs/{job_id}/extract/topics
GET  /api/jobs/{job_id}/topics
PUT  /api/jobs/{job_id}/topics
POST /api/jobs/{job_id}/topics/approve

POST /api/jobs/{job_id}/extract/lessons
GET  /api/jobs/{job_id}/lessons
PUT  /api/jobs/{job_id}/lessons
POST /api/jobs/{job_id}/lessons/approve

POST   /api/jobs/{job_id}/extract/chunks
GET    /api/jobs/{job_id}/chunks
PUT    /api/jobs/{job_id}/chunks
POST   /api/jobs/{job_id}/chunks/add
DELETE /api/jobs/{job_id}/chunks/{chunk_id}
POST   /api/jobs/{job_id}/chunks/recut
POST   /api/jobs/{job_id}/chunks/approve

POST /api/jobs/{job_id}/prepare-bundle
GET  /api/jobs/{job_id}/bundle
GET  /api/jobs/{job_id}/bundle/download

POST /api/jobs/{job_id}/import-mongodb
GET  /api/jobs/{job_id}/mongo-import-result
```

## Next Phases

1. Phase 13: optional full_auto mode
