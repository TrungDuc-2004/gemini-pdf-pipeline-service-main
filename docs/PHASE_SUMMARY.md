# Phase Summary

This document records the completed implementation phases for the standalone `gemini-pdf-pipeline-service`.

## Current Verified Job

- Job ID: `7f243448-4e57-4133-9137-f7a87c5030fc`
- PDF: `Tin-hoc-11-ket-noi-tri-thuc.pdf`
- Topics: 7
- Lessons: 31
- Chunks: 70
- Topic PDFs: 7
- Lesson PDFs: 31
- Chunk PDFs: 70
- MongoDB documents:
  - `class`: 1
  - `subject`: 1
  - `topic`: 7
  - `lesson`: 31
  - `chunk`: 70
  - `keyword`: 5
  - `chunk_keyword`: 5

Keyword extraction is partially populated for this verified job. Some Gemini keys entered cooldown during keyword extraction, so bundle recovery was verified with `skip_keywords=true`. MongoDB import safely skips empty, placeholder, invalid, and error keyword files.

## Phase 1: Project Skeleton + Job Upload/Status/Log

Goal:
Create the standalone FastAPI project skeleton and basic disk-based job lifecycle.

Result:
- Added FastAPI app structure.
- Added `workspace/`, `output/`, `logs/`, `frontend/review-ui/`, and `fake_backend/` placeholders.
- Implemented PDF upload, job config/state/progress/result files, and job log files.

Verified status:
- `GET /health` works.
- `POST /api/jobs` creates a job.
- `GET /api/jobs/{job_id}`, `/status`, and `/logs` work.

Important endpoints:
- `GET /health`
- `POST /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/status`
- `GET /api/jobs/{job_id}/logs`

Remaining TODOs:
- Replace in-process background execution with a production worker queue later.

## Phase 2: Local Gemini Key Rotation

Goal:
Add standalone Gemini key management without importing old backend code.

Result:
- Added local `GeminiKeyManager`.
- Supports `GEMINI_API_KEYS` and numbered `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, etc.
- Persists rotation state in `workspace/gemini_rotation_state.json`.
- Tracks cooldowns, dead keys, and last errors.
- Added debug endpoints that never expose real key values.

Verified status:
- Debug key endpoint works with no keys and with configured keys.
- Manual rotate/cooldown/dead/clear actions work for local testing.

Important endpoints:
- `GET /api/debug/gemini-keys`
- `POST /api/debug/gemini-keys/rotate`
- `POST /api/debug/gemini-keys/mark-current-cooldown`
- `POST /api/debug/gemini-keys/mark-current-dead`
- `POST /api/debug/gemini-keys/clear-state`

Remaining TODOs:
- Add operational metrics for key health if needed.

## Phase 3: Topic Extraction

Goal:
Migrate topic extraction from the old `gemini_pipeline` into the standalone service.

Result:
- Added local Gemini client and runner.
- Adapted prompt, PDF preview, manifest normalization, and Topic/Lesson PDF artifact generation logic.
- Added topic extraction service and topic review APIs.

Verified status:
- Real Gemini topic extraction succeeded for the verified PDF.
- Produced 7 topic artifacts and 31 lesson artifacts.

Important endpoints:
- `POST /api/jobs/{job_id}/extract/topics`
- `GET /api/jobs/{job_id}/topics`
- `PUT /api/jobs/{job_id}/topics`
- `POST /api/jobs/{job_id}/topics/approve`

Remaining TODOs:
- Improve structured logging around Gemini schema validation.

## Phase 3.1: Real Topic Extraction Verification

Goal:
Run the Phase 3 implementation against a real PDF and real Gemini keys.

Result:
- Created a real job from `Tin-hoc-11-ket-noi-tri-thuc.pdf`.
- Topic extraction reached `reviewing_topics`.
- Topic GET/PUT/approve flow worked.

Verified status:
- Topics: 7
- Generated Topic artifacts: 7
- Generated Lesson artifacts from topic extraction stage: 31

Remaining TODOs:
- Keep Gemini keys rotated and avoid committing secrets.

## Phase 4: Topic Review UI

Goal:
Create a practical React review UI for topic review.

Result:
- Added Vite React app under `frontend/review-ui`.
- Added job creation, job detail, topic review, status, log, and raw JSON panels.
- Added CORS support for local frontend development.

Verified status:
- Frontend build completed.
- Topic UI can create/load jobs, start topic extraction, edit/save topics, approve topics, and view logs/raw JSON.

Remaining TODOs:
- Add PDF preview panes in a later UI phase if needed.

## Phase 5: Lesson Extraction

Goal:
Implement lesson extraction/rebuild after topic approval.

Result:
- Added lesson service and lesson APIs.
- Reads `approved_topics.json` and raw lesson state.
- Rebuilds canonical Topic and Lesson artifacts.
- Writes `lessons_partial.json` and supports lesson approval.

Verified status:
- Smoke test succeeded on the verified job.
- Lesson count: 31.
- Topic artifacts remained valid after lesson rebuild.

Important endpoints:
- `POST /api/jobs/{job_id}/extract/lessons`
- `GET /api/jobs/{job_id}/lessons`
- `PUT /api/jobs/{job_id}/lessons`
- `POST /api/jobs/{job_id}/lessons/approve`

Remaining TODOs:
- Add deeper validation for manually edited lesson ranges.

## Phase 6: Lesson Review UI

Goal:
Add lesson review UI without breaking topic review.

Result:
- Added `LessonReviewView`.
- Added lesson API client functions.
- Added grouped-by-topic lesson layout.
- Reused status, log, and raw JSON panels.

Verified status:
- Frontend build completed.
- Existing verified job can load 31 lessons grouped by topic.

Remaining TODOs:
- Add optional PDF preview and page-range validation in the UI.

## Phase 7: Chunk Extraction

Goal:
Implement chunk extraction after lesson approval.

Result:
- Adapted old chunk pipeline logic.
- Added chunk extraction service and chunk APIs.
- Generates `Chunk/` artifacts and `.keywords.json` placeholders.
- Supports save, add, delete, recut, and approve APIs.

Verified status:
- Smoke test succeeded on the verified job.
- Chunk count: 70.
- Lesson group count: 31.
- Topic and Lesson artifacts remained valid.

Important endpoints:
- `POST /api/jobs/{job_id}/extract/chunks`
- `GET /api/jobs/{job_id}/chunks`
- `PUT /api/jobs/{job_id}/chunks`
- `POST /api/jobs/{job_id}/chunks/add`
- `DELETE /api/jobs/{job_id}/chunks/{chunk_id}`
- `POST /api/jobs/{job_id}/chunks/recut`
- `POST /api/jobs/{job_id}/chunks/approve`

Remaining TODOs:
- Improve partial-failure reporting if a single lesson chunk extraction fails.

## Phase 8: Chunk Review UI

Goal:
Add chunk review UI without breaking topic or lesson review.

Result:
- Added `ChunkReviewView`.
- Added chunk API client functions.
- Added grouped-by-lesson layout and chunk table.
- Added add/delete/recut/save/approve controls.

Verified status:
- Frontend build completed.
- Verified job can load 70 chunks grouped across 31 lessons.

Remaining TODOs:
- Add richer PDF recut preview and guardrails around manual chunk edits.

## Phase 9: Prepare Bundle + Keyword Extraction

Goal:
Prepare final output bundle after chunk approval and optionally run keyword extraction.

Result:
- Added bundle service and keyword service.
- Copies reviewed workspace bundle to `output/{book_stem}/`.
- Validates Topic/Lesson/Chunk counts and files.
- Runs keyword extraction when enabled.
- Adds bundle summary and ZIP download endpoints.

Verified status:
- Bundle copy/finalization worked.
- Core output counts were correct:
  - Topics: 7
  - Lessons: 31
  - Chunks: 70
  - Topic PDFs: 7
  - Lesson PDFs: 31
  - Chunk PDFs: 70

Important endpoints:
- `POST /api/jobs/{job_id}/prepare-bundle`
- `GET /api/jobs/{job_id}/bundle`
- `GET /api/jobs/{job_id}/bundle/download`

Remaining TODOs:
- Move long keyword extraction to a worker queue later.

## Phase 9.1: Recovery `skip_keywords` + Bundle Ready/Download

Goal:
Fix prepare-bundle recovery when keyword extraction fails or keys are cooling down.

Result:
- Added safe keyword reuse.
- Added `skip_keywords=true` one-run override.
- Added `retry_failed_keywords_only=true`.
- Fixed bundle validation to count only real chunk metadata JSON.
- Ensured successful reruns recover job status from `error` to `bundle_ready`.

Verified status:
- Recovery with `skip_keywords=true` succeeded.
- `GET /api/jobs/{job_id}/bundle` returned `ok=true`.
- `GET /api/jobs/{job_id}/bundle/download` returned a ZIP.

Remaining TODOs:
- Retry failed keyword files after Gemini cooldowns expire.

## Phase 10: MongoDB Import Adapter

Goal:
Import the prepared bundle into MongoDB for integration testing.

Result:
- Added MongoDB import service.
- Added idempotent upserts using stable `import_key` values.
- Imports class, subject, topic, lesson, chunk, keyword, chunk_keyword, and import_job documents.
- Skips empty, placeholder, invalid, or error keyword files without failing the full import.

Verified status:
- MongoDB import succeeded for the verified job.
- Import is idempotent.

Verified counts:
- `class`: 1
- `subject`: 1
- `topic`: 7
- `lesson`: 31
- `chunk`: 70
- `keyword`: 5
- `chunk_keyword`: 5
- `import_job`: 1

Important endpoints:
- `POST /api/jobs/{job_id}/import-mongodb`
- `GET /api/jobs/{job_id}/mongo-import-result`

Remaining TODOs:
- Superseded by Phase 13 for Metadata-Edu schema and MinIO upload.

## Phase 11: Fake Backend Integration

Goal:
Create a minimal backend that demonstrates HTTP integration with the standalone pipeline service.

Result:
- Added `fake_backend/`.
- Fake backend calls the pipeline service over HTTP only.
- Supports manual proxy endpoints for job creation, extraction, review approval, bundle prep, and Mongo import.
- Added no-review demo helper endpoints for integration testing.

Verified status:
- Fake backend health/status/bundle/mongo-result endpoints were smoke-tested against the existing verified pipeline job.
- The full no-review helper exists but has not been fully end-to-end tested.

Important endpoints:
- `GET /health`
- `POST /demo/jobs`
- `GET /demo/jobs/{job_id}/status`
- `POST /demo/jobs/{job_id}/extract-topics`
- `POST /demo/jobs/{job_id}/approve-topics`
- `POST /demo/jobs/{job_id}/extract-lessons`
- `POST /demo/jobs/{job_id}/approve-lessons`
- `POST /demo/jobs/{job_id}/extract-chunks`
- `POST /demo/jobs/{job_id}/approve-chunks`
- `POST /demo/jobs/{job_id}/prepare-bundle`
- `POST /demo/jobs/{job_id}/import-mongodb`
- `POST /demo/jobs/{job_id}/run-until-bundle-ready-no-review`
- `POST /demo/upload-and-run-no-review`

Remaining TODOs:
- Fully test no-review helper with a fresh PDF job.
- Add authentication if the fake backend evolves beyond local integration testing.

## Phase 12: Optional Kaggle OCR/Cutline

Goal:
Add optional Kaggle OCR/cutline post-processing after chunks are reviewed and before keyword extraction.

Result:
- Added service-local Kaggle postprocess adapter.
- Added `skip_kaggle=true` prepare-bundle override.
- Added service-local `kaggle_pack/` and `output/_kaggle_outputs/{kernel_slug}/downloads/` usage.
- Added local copies of the Kaggle kernel script and `chunk_postprocess.py`.
- Preserved stale dataset and wrong-book safety checks from the old pipeline.
- Hardened the kernel script with an unhandled-exception status hook.

Verified status:
- Safe recovery path was tested on the verified job:
  - `POST /api/jobs/{job_id}/prepare-bundle?skip_kaggle=true&skip_keywords=true`
  - status returned to `bundle_ready`
  - bundle summary remained valid
  - ZIP download returned HTTP 200
- Actual Kaggle execution was not run in this phase because it requires configured Kaggle credentials and a job created with `enable_kaggle=true`.

Important endpoints:
- `POST /api/jobs/{job_id}/prepare-bundle?skip_kaggle=true`
- `GET /api/jobs/{job_id}/bundle`
- `GET /api/jobs/{job_id}/bundle/download`

Remaining TODOs:
- Run a real Kaggle postprocess job once Kaggle credentials and kernel/dataset ownership are confirmed.
- Keep Kaggle as optional until production runtime expectations are stable.

## Phase 13: Metadata-Edu MongoDB Import + MinIO Upload

Goal:
Store final review-first bundles according to the Metadata-Edu thesis schema and upload generated PDFs to MinIO.

Result:
- Added MinIO configuration and `minio` dependency.
- Added `app/services/minio_service.py`.
- Added `app/services/metadata_edu_import_service.py`.
- Updated `POST /api/jobs/{job_id}/import-mongodb` to use the Metadata-Edu importer by default.
- Preserved the older temporary importer in `mongo_import_service.py`.
- Writes class, subject, topic, lesson, chunk, asset, keyword, keyword_alias, chunk_keyword, topic_bag, and import_job collections.
- Uploads subject/topic/lesson/chunk PDFs under `documents/lop-{grade}/tin-hoc/...`.
- Writes `workspace/{job_id}/logs/mongo_import.log` and `workspace/{job_id}/logs/minio_upload.log`.

Verified status:
- Smoke-tested with job `aad50085-93a9-41ba-9e6d-b14b412e7684`.
- First import succeeded with 109 uploaded PDFs and 109 asset documents for this job.
- Second import succeeded with stable MongoDB counts, confirming idempotent upserts for the tested bundle.

Verified counts for the smoke job:
- `class`: 1
- `subject`: 1
- `topic`: 7
- `lesson`: 31
- `chunk`: 70
- `asset`: 109
- `keyword`: 0
- `chunk_keyword`: 0
- `topic_bag`: 0

Keyword count is zero for this smoke job because all 70 keyword files were empty placeholders.

Important endpoints:
- `POST /api/jobs/{job_id}/import-mongodb?upload_minio=true&dry_run=false`
- `POST /api/jobs/{job_id}/import-mongodb?upload_minio=false`
- `POST /api/jobs/{job_id}/import-mongodb?dry_run=true`
- `GET /api/jobs/{job_id}/mongo-import-result`

Remaining TODOs:
- Add richer verification fixtures for non-empty keyword files and aliases.

## Next Phases After Phase 13

- Phase 14: Optional `full_auto` mode.
- Future production hardening: worker queue, auth, observability, and deployment configuration.
