# Development

## Local Setup

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Example `.env`:

```env
APP_NAME=gemini-pdf-pipeline-service
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8100
WORKSPACE_DIR=./workspace
OUTPUT_DIR=./output
LOG_DIR=./logs

GEMINI_MODEL=gemini-2.5-flash
GEMINI_API_KEYS=your_key_1,your_key_2

MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=data-ai-tra-cuu

MINIO_ENDPOINT=http://127.0.0.1:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ai-tra-cuu
MINIO_SECURE=false
MINIO_PUBLIC_URL=http://127.0.0.1:9000

ENABLE_KAGGLE=false
KAGGLE_USERNAME=
KAGGLE_KEY=
KAGGLE_KERNEL_REF=dat261303/debug-cutlines-auto
KAGGLE_DATASET_ID=dat261303/kaggle-pack
KAGGLE_MAX_ATTEMPTS=3
KAGGLE_POLL_SECONDS=20
```

Never commit real API keys.

Kaggle is optional. It runs after chunk approval during `prepare-bundle`, not during initial chunk extraction. Set `enable_kaggle=true` when creating a job to enable it for that job. Use `skip_kaggle=true` on a prepare-bundle request to bypass it for recovery/testing.

## Run Backend

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

Docs:

```text
http://localhost:8100/docs
```

## Run Review UI

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

## Run Fake Backend

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service/fake_backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8200 --reload
```

Open:

```text
http://localhost:8200/docs
```

## MongoDB

Run MongoDB locally with your normal install. Example:

```bash
brew services start mongodb-community
```

Check that it is listening:

```bash
lsof -iTCP:27017 -sTCP:LISTEN
```

## Gemini Key Debug

Safe key status:

```bash
curl http://localhost:8100/api/debug/gemini-keys
```

Rotate manually:

```bash
curl -X POST http://localhost:8100/api/debug/gemini-keys/rotate
```

Clear local runtime state:

```bash
curl -X POST http://localhost:8100/api/debug/gemini-keys/clear-state
```

The debug API never returns actual key values.

## Checks

Backend compile:

```bash
python3 -m compileall app
```

Fake backend compile:

```bash
cd fake_backend
python3 -m compileall .
```

Frontend build:

```bash
cd frontend/review-ui
npm run build
```

## Troubleshooting

### Service Not Running

If curl returns connection refused, start the service:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

### Missing Gemini Keys

Set one of:

```env
GEMINI_API_KEYS=your_key_1,your_key_2
```

or:

```env
GEMINI_API_KEY_1=your_key_1
GEMINI_API_KEY_2=your_key_2
```

### Keys Reported as Leaked

If real keys were pasted into chat, logs, screenshots, or committed files, rotate them in the provider console. This repo should only contain placeholders.

### All Keys Cooldown

The key manager returns a next available time. Wait until cooldown expires or add a valid key, then retry. For bundle recovery without keyword calls:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?skip_keywords=true"
```

### Bundle Not Ready

Check status and result:

```bash
curl http://localhost:8100/api/jobs/{job_id}/status
cat workspace/{job_id}/result.json
```

If chunks are approved and only keywords are blocked, use `skip_keywords=true`.

If Kaggle is enabled and blocking recovery, use:

```bash
curl -X POST "http://localhost:8100/api/jobs/{job_id}/prepare-bundle?skip_kaggle=true&skip_keywords=true"
```

## MongoDB Target Database

Recommended local development target:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=data-ai-tra-cuu
```

Earlier verification used `gemini_pipeline_test`; keep it intact unless you explicitly choose to clean it. MongoDB creates `data-ai-tra-cuu` automatically on first insert.

After editing `.env`, restart FastAPI:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

Verify in MongoDB Compass by opening `data-ai-tra-cuu` and checking `class`, `subject`, `topic`, `lesson`, `chunk`, `keyword`, `chunk_keyword`, and `import_job`.

## MinIO

Run MinIO locally and configure the pipeline service with:

```env
MINIO_ENDPOINT=http://127.0.0.1:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ai-tra-cuu
MINIO_SECURE=false
MINIO_PUBLIC_URL=http://127.0.0.1:9000
```

The importer creates the bucket if it does not exist. Verify uploads in the MinIO console by checking:

```text
ai-tra-cuu/documents/lop-11/tin-hoc/subject/
ai-tra-cuu/documents/lop-11/tin-hoc/topic/
ai-tra-cuu/documents/lop-11/tin-hoc/lesson/
ai-tra-cuu/documents/lop-11/tin-hoc/chunk/
```

MinIO upload logs are written to:

```text
workspace/{job_id}/logs/minio_upload.log
```

### MongoDB Connection Refused

Start MongoDB and verify `.env`:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=data-ai-tra-cuu
```

### CORS Issue

The backend currently allows:

```text
http://localhost:5173
http://127.0.0.1:5173
```

Use those frontend URLs during development.

### Keyword Files Empty

Empty keyword files are allowed. MongoDB import skips them and imports topics, lessons, and chunks normally. Use `retry_failed_keywords_only=true` later to refill missing keywords.

### Kaggle CLI Missing

Install the Kaggle CLI dependency and verify:

```bash
pip install -r requirements.txt
kaggle --version
```

### Kaggle Credentials Missing

Set environment variables:

```env
KAGGLE_USERNAME=your_username
KAGGLE_KEY=your_key
```

or configure:

```text
~/.kaggle/kaggle.json
```

Do not commit Kaggle credentials.

### `stale_dataset_mismatch`

The kernel resolved a different `book_stem` than the request expected. The service writes `workspace/{job_id}/kaggle_result.json` and retries bounded attempts. If it persists, rerun prepare-bundle after dataset propagation or use `skip_kaggle=true`.

### Expected Kaggle ZIP Missing

Check:

```text
workspace/{job_id}/logs/kaggle.log
output/_kaggle_outputs/{kernel_slug}/downloads/
workspace/{job_id}/kaggle_result.json
```

The service expects a request-specific ZIP named like:

```text
{book_stem}_{request_id}_postprocessed.zip
```

### Wrong Book Stem in Kaggle ZIP

The service validates the ZIP top-level folder before applying. If the folder does not match the expected `book_stem`, the ZIP is rejected and the job moves to `error`.

### Kaggle Kernel Failed

Inspect:

```text
workspace/{job_id}/logs/kaggle.log
output/_kaggle_outputs/{kernel_slug}/downloads/current_run_status*.json
```

The kernel script writes `current_run_status.json` and `current_run_status_{request_id}.json`, including unhandled exception status when possible.

### Kaggle Dependency Install Failed

The kernel installs OCR dependencies on Kaggle, not locally. Network or package version issues can fail the kernel. Rerun later or use `skip_kaggle=true` to prepare and import the non-postprocessed bundle.

### Job Stuck in Error

Inspect:

```text
workspace/{job_id}/progress.json
workspace/{job_id}/result.json
workspace/{job_id}/logs/job.log
```

Then rerun the relevant stage only after fixing the cause. For final bundle recovery, use `prepare-bundle?skip_keywords=true`.
