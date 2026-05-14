# Review UI

Simple React/Vite UI for the review-first topic, lesson, and chunk workflow.

## Setup

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service/frontend/review-ui
npm install
cp .env.example .env
```

## Run

Backend:

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

Frontend:

```bash
cd /Users/tt/Documents/gemini-pdf-pipeline-service/frontend/review-ui
npm run dev
```

Open:

```text
http://localhost:5173
```

## Manual Flow

1. Create a job with a PDF.
2. Click `Trích xuất chủ đề`.
3. Wait until status becomes `reviewing_topics`.
4. Click `Tải danh sách chủ đề`.
5. Edit topic rows if needed.
6. Click `Lưu chỉnh sửa`.
7. Click `Duyệt chủ đề`.
8. Click `Trích xuất bài học`.
9. Wait until status becomes `reviewing_lessons`.
10. Click `Tải danh sách bài học`.
11. Review lessons grouped by topic.
12. Click `Lưu bài học`.
13. Click `Duyệt bài học`.
14. Click `Trích xuất chunk`.
15. Wait until status becomes `reviewing_chunks`.
16. Click `Tải danh sách chunk`.
17. Review chunks grouped by lesson.
18. Use `Thêm chunk`, `Xóa chunk`, or `Cắt lại chunk` only when needed.
19. Click `Lưu chunk`.
20. Click `Duyệt chunk`.
21. Use log and raw JSON panels for debugging.

## Chunk Review Notes

The chunk screen is intentionally debug-focused:

- left column: `Danh sách bài học` with chunk counts
- middle column: editable `Chunk trong bài học` table
- right column: selected chunk detail, raw JSON, and processing logs

The UI calls these backend endpoints:

```text
POST   /api/jobs/{job_id}/extract/chunks
GET    /api/jobs/{job_id}/chunks
PUT    /api/jobs/{job_id}/chunks
POST   /api/jobs/{job_id}/chunks/add
DELETE /api/jobs/{job_id}/chunks/{chunk_id}
POST   /api/jobs/{job_id}/chunks/recut
POST   /api/jobs/{job_id}/chunks/approve
```
