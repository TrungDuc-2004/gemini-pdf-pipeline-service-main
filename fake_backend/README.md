# Fake Backend

This is a minimal integration-test backend that demonstrates how another backend can call `gemini-pdf-pipeline-service` through HTTP APIs.

It does not import pipeline internals. It only knows the pipeline service URL and forwards requests over HTTP.

## Setup

Run the pipeline service first:

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

Run this fake backend:

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

## Health

```bash
curl http://localhost:8200/health
```

## Manual Review-First Flow

Create a job:

```bash
curl -X POST http://localhost:8200/demo/jobs \
  -F "file=@/absolute/path/to/book.pdf;type=application/pdf" \
  -F "book_name=Tin học 11 Kết nối tri thức" \
  -F "class_name=11" \
  -F "subject_name=Tin học" \
  -F "subject_type=Kết nối tri thức" \
  -F "enable_keywords=true" \
  -F "enable_kaggle=false"
```

Then call each stage:

```bash
curl -X POST http://localhost:8200/demo/jobs/{job_id}/extract-topics
curl http://localhost:8200/demo/jobs/{job_id}/status
curl http://localhost:8200/demo/jobs/{job_id}/topics
curl -X POST http://localhost:8200/demo/jobs/{job_id}/approve-topics

curl -X POST http://localhost:8200/demo/jobs/{job_id}/extract-lessons
curl http://localhost:8200/demo/jobs/{job_id}/lessons
curl -X POST http://localhost:8200/demo/jobs/{job_id}/approve-lessons

curl -X POST http://localhost:8200/demo/jobs/{job_id}/extract-chunks
curl http://localhost:8200/demo/jobs/{job_id}/chunks
curl -X POST http://localhost:8200/demo/jobs/{job_id}/approve-chunks

curl -X POST "http://localhost:8200/demo/jobs/{job_id}/prepare-bundle?skip_keywords=true"
curl http://localhost:8200/demo/jobs/{job_id}/bundle
curl -X POST http://localhost:8200/demo/jobs/{job_id}/import-mongodb
curl http://localhost:8200/demo/jobs/{job_id}/mongo-import-result
```

## No-Review Demo Helper

This helper approves current stage outputs as-is. It is for integration testing only and is not the real `full_auto` mode.

```bash
curl -X POST "http://localhost:8200/demo/jobs/{job_id}/run-until-bundle-ready-no-review?skip_keywords=true"
```

Upload and run the same no-review helper:

```bash
curl -X POST "http://localhost:8200/demo/upload-and-run-no-review?import_mongodb=false" \
  -F "file=@/absolute/path/to/book.pdf;type=application/pdf" \
  -F "book_name=Tin học 11 Kết nối tri thức" \
  -F "class_name=11" \
  -F "subject_name=Tin học" \
  -F "subject_type=Kết nối tri thức"
```

## Existing Job Smoke Checks

```bash
curl http://localhost:8200/demo/jobs/7f243448-4e57-4133-9137-f7a87c5030fc/status
curl http://localhost:8200/demo/jobs/7f243448-4e57-4133-9137-f7a87c5030fc/bundle
curl http://localhost:8200/demo/jobs/7f243448-4e57-4133-9137-f7a87c5030fc/mongo-import-result
```

## Limitations

- This backend is only a demo client.
- It does not store its own database state.
- It does not perform human review edits.
- The no-review helper can still take a long time because it waits for Gemini extraction stages.
- Pipeline errors are intentionally forwarded instead of hidden.
