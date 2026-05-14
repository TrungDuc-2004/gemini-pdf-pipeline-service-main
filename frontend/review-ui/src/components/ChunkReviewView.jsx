import { useMemo, useState } from "react";
import { getChunkPreviewUrl, getSourcePreviewUrl } from "../api/reviewApi.js";
import EmptyState from "./EmptyState.jsx";
import ErrorState from "./ErrorState.jsx";
import LoadingState from "./LoadingState.jsx";
import SplitPdfReview from "./SplitPdfReview.jsx";

function groupsFrom(chunks, groupedByLesson) {
  if (Array.isArray(groupedByLesson) && groupedByLesson.length) return groupedByLesson;
  const map = new Map();
  for (const chunk of Array.isArray(chunks) ? chunks : []) {
    const key = `${chunk.lesson_stem || ""}-${chunk.lesson_num || ""}-${chunk.lesson_name || ""}`;
    if (!map.has(key)) map.set(key, { lesson_num: chunk.lesson_num, lesson_name: chunk.lesson_name, lesson_stem: chunk.lesson_stem, chunks: [] });
    map.get(key).chunks.push(chunk);
  }
  return Array.from(map.values());
}

function chunkStatus(chunk) {
  if (chunk?.error) return { label: "Lỗi", tone: "danger" };
  if (chunk?.kaggle_finalized && chunk?.metadata_edu_saved && chunk?.minio_uploaded) return { label: "Đã lưu sau Kaggle", tone: "done" };
  if (chunk?.kaggle_finalized) return { label: "Đã finalize", tone: "done" };
  if (chunk?.waiting_for_kaggle || chunk?.approved) return { label: "Đã duyệt - Chờ Kaggle", tone: "warning" };
  return { label: "Chờ duyệt", tone: "pending" };
}

function pad2(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? String(parsed).padStart(2, "0") : String(value || "--");
}

export default function ChunkReviewView({
  jobId,
  chunks,
  groupedByLesson,
  approved,
  loading,
  status,
  jobStatus,
  error,
  onChange,
  onLoad,
  onExtract,
  onSave,
  onApprove,
  onApproveChunk,
  onApproveChunkIds,
  onAdd,
  onDelete,
  onRecut,
  onBack,
  onNext,
}) {
  const safeChunks = Array.isArray(chunks) ? chunks : [];
  const groups = useMemo(() => groupsFrom(safeChunks, groupedByLesson), [safeChunks, groupedByLesson]);
  const [selectedGroupIndex, setSelectedGroupIndex] = useState(0);
  const [selectedChunkId, setSelectedChunkId] = useState("");
  const [toolsOpen, setToolsOpen] = useState(false);
  const [addForm, setAddForm] = useState({ chunk_num: "", chunk_name: "", title: "", heading: "", start: 1, end: 1, content_head: false });
  const selectedGroup = groups[Math.min(selectedGroupIndex, Math.max(groups.length - 1, 0))] || { chunks: [] };
  const selectedChunk = selectedGroup.chunks.find((chunk) => (chunk.chunk_id || chunk.id) === selectedChunkId) || selectedGroup.chunks[0] || null;
  const selectedChunkIndex = selectedChunk ? safeChunks.findIndex((item) => item === selectedChunk || item.chunk_id === selectedChunk.chunk_id || item.id === selectedChunk.id) : -1;
  const approvedCount = safeChunks.filter((chunk) => chunk.approved || chunk.waiting_for_kaggle || chunk.kaggle_finalized || chunk.metadata_edu_saved).length;
  const selectedChunkPreviewId = selectedChunk ? (selectedChunk.chunk_id || selectedChunk.id) : "";
  const sourcePreviewUrl = jobId ? getSourcePreviewUrl(jobId) : "";
  const chunkPreviewUrl = jobId && selectedChunkPreviewId ? getChunkPreviewUrl(jobId, selectedChunkPreviewId) : "";
  const waitingCount = safeChunks.filter((chunk) => chunk.waiting_for_kaggle || (chunk.approved && !chunk.kaggle_finalized)).length;
  const finalizedCount = safeChunks.filter((chunk) => chunk.kaggle_finalized || (chunk.metadata_edu_saved && chunk.minio_uploaded)).length;
  const isExtracting = jobStatus === "extracting_chunks" || status?.status === "extracting_chunks";

  function chunkId(chunk, index) {
    return chunk.chunk_id || chunk.id || `${chunk.lesson_stem || "lesson"}:${chunk.chunk_num || index}`;
  }

  function updateAdd(field, value) {
    setAddForm((current) => ({ ...current, [field]: value }));
  }

  function updateSelected(field, value) {
    if (selectedChunkIndex >= 0) onChange(selectedChunkIndex, field, value);
  }

  async function submitAdd(event) {
    event.preventDefault();
    if (!onAdd || (!selectedGroup.lesson_stem && !selectedGroup.lesson_num)) return;
    await onAdd({
      ...addForm,
      lesson_stem: selectedGroup.lesson_stem,
      lesson_num: selectedGroup.lesson_num,
      start: Number(addForm.start),
      end: Number(addForm.end),
    });
  }

  return (
    <section className="panel reviewCard chunkReviewWorkspace">
      <div className="topicReviewHeader">
        <div>
          <span className="stepLabel">Bước 4</span>
          <h2>Bước 4: Duyệt chunk</h2>
          <p className="muted">Đối chiếu sách gốc với chunk được AI cắt ra. Chunk chỉ được lưu chính thức sau khi Kaggle xử lý.</p>
        </div>
        <div className="topicHeaderActions">
          <button type="button" className={safeChunks.length ? "secondary-action" : "primaryButton"} onClick={onExtract} disabled={loading}>
            {safeChunks.length ? "Trích xuất lại chunk" : "Trích xuất chunk"}
          </button>
          <button type="button" className="secondary-action" onClick={onLoad} disabled={loading}>Tải lại</button>
        </div>
      </div>
      {safeChunks.length > 0 ? (
        <div className="summaryCards">
          <div className="summaryCard"><span>Tổng chunk</span><strong>{safeChunks.length}</strong></div>
          <div className="summaryCard"><span>Đã duyệt</span><strong>{approvedCount}</strong></div>
          <div className="summaryCard"><span>Chờ Kaggle</span><strong>{waitingCount}</strong></div>
          <div className="summaryCard"><span>Đã lưu sau Kaggle</span><strong>{finalizedCount}</strong></div>
        </div>
      ) : null}
      {safeChunks.length > 0 ? <p className="infoNote">Chunk đã duyệt vẫn là dữ liệu tạm cho tới khi bước Kaggle và finalize hoàn tất.</p> : null}
      {safeChunks.length > 0 && approvedCount === 0 ? (
        <p className="infoNote neutral">Hãy duyệt ít nhất một chunk trước khi chuyển sang bước hoàn tất.</p>
      ) : null}
      {approvedCount > 0 ? (
        <div className="successBox inlineSuccess">Đã duyệt {approvedCount}/{safeChunks.length} chunk. Các chunk đã duyệt đang chờ Kaggle xử lý.</div>
      ) : null}
      {loading && !isExtracting ? <LoadingState message="Đang tải chunk..." /> : null}
      {error ? <ErrorState message={error} onRetry={onLoad} /> : null}
      {isExtracting && safeChunks.length === 0 ? (
        <ProcessingState
          title="Đang trích xuất chunk"
          message="Hệ thống đang tạo các chunk tạm từ bài học đã duyệt. Chunk chính thức sẽ được lưu sau bước Kaggle."
          status={status}
        />
      ) : null}
      {!isExtracting && !loading && !error && chunks == null ? <EmptyState message="Chưa có dữ liệu chunk. Hãy duyệt bài học trước khi trích xuất chunk." /> : null}
      {!isExtracting && !loading && !error && chunks != null && safeChunks.length === 0 ? <EmptyState message="Danh sách chunk đang trống." /> : null}
      {groups.length > 0 ? (
        <div className="review-workspace">
          <aside className="review-navigator">
            <div className="cardHeaderCompact">
              <h3>Danh sách chunk</h3>
              <span>{groups.length} lesson</span>
            </div>
            {groups.map((group, groupIndex) => (
              <div className="navigatorGroup" key={`${group.lesson_stem}-${groupIndex}`}>
                <div className="navigatorGroupTitle">
                  <strong>{group.lesson_num ? `Lesson ${pad2(group.lesson_num)}` : "Lesson"}</strong>
                  <span>{group.chunks?.length || 0} chunk</span>
                </div>
                {(group.chunks || []).map((chunk, localIndex) => {
                  const id = chunkId(chunk, localIndex);
                  const status = chunkStatus(chunk);
                  return (
                    <button
                      type="button"
                      key={id}
                      className={`review-list-item ${selectedChunk && chunkId(selectedChunk, localIndex) === id ? "review-list-item-active" : ""}`}
                      onClick={() => {
                        setSelectedGroupIndex(groupIndex);
                        setSelectedChunkId(id);
                      }}
                    >
                      <strong>Chunk {pad2(chunk.chunk_num)}</strong>
                      <span>{chunk.chunk_name || chunk.title || "-"}</span>
                      <small>Trang {chunk.start || "-"}-{chunk.end || "-"}</small>
                      <em className={`status-chip ${status.tone}`}>{status.label}</em>
                    </button>
                  );
                })}
              </div>
            ))}
          </aside>

          <SplitPdfReview
            title="Đối chiếu bản gốc và bản cắt"
            description="Sách giáo khoa gốc ở bên trái, chunk đã trích xuất ở bên phải."
            sourcePreviewUrl={sourcePreviewUrl}
            extractedPreviewUrl={chunkPreviewUrl}
            sourceLabel="Sách giáo khoa gốc"
            extractedLabel="Chunk đã trích xuất"
            sourcePageHint="Bản PDF nguồn của phiên duyệt"
            extractedPageHint={selectedChunk ? `${selectedChunk.chunk_name || selectedChunk.title || "Chunk"} · Trang ${selectedChunk.start || "-"}-${selectedChunk.end || "-"}` : ""}
            extractedStatusBadge={selectedChunk ? <span className={`status-chip ${chunkStatus(selectedChunk).tone}`}>{chunkStatus(selectedChunk).label}</span> : null}
            missingExtractedMessage="Chưa tìm thấy file preview chunk."
            missingExtractedDetail="Nếu chunk vừa được trích xuất, vui lòng chờ thêm hoặc mở Debug để kiểm tra log."
          >
            <section className="review-editor">
              <h3>Metadata chunk</h3>
            {selectedChunk ? (
              <>
                <div className="metadataForm">
                  <label>
                    <span>Số chunk</span>
                    <input value={selectedChunk.chunk_num ?? ""} disabled={approved || selectedChunk.approved} onChange={(event) => updateSelected("chunk_num", event.target.value)} />
                  </label>
                  <label>
                    <span>Tên chunk</span>
                    <input value={selectedChunk.chunk_name ?? ""} disabled={approved || selectedChunk.approved} onChange={(event) => updateSelected("chunk_name", event.target.value)} />
                  </label>
                  <div className="twoColumn">
                    <label>
                      <span>Trang bắt đầu</span>
                      <input type="number" value={selectedChunk.start ?? ""} disabled={approved || selectedChunk.approved} onChange={(event) => updateSelected("start", Number(event.target.value))} />
                    </label>
                    <label>
                      <span>Trang kết thúc</span>
                      <input type="number" value={selectedChunk.end ?? ""} disabled={approved || selectedChunk.approved} onChange={(event) => updateSelected("end", Number(event.target.value))} />
                    </label>
                  </div>
                </div>
                {selectedChunk.approved || selectedChunk.waiting_for_kaggle ? (
                  <div className="warningBox inlineNotice">Chunk đã duyệt và đang chờ Kaggle xử lý trước khi lưu chính thức.</div>
                ) : null}
                {selectedChunk.metadata_edu_saved || selectedChunk.minio_uploaded ? (
                  <div className="successBox">Đã lưu chunk vào MongoDB/MinIO.</div>
                ) : null}
                <div className="approvalActions">
                  <button type="button" className="primaryButton primary-action" onClick={() => onApproveChunk?.(selectedChunk)} disabled={loading || selectedChunk.approved}>
                    {selectedChunk.approved ? "Đã duyệt" : "Duyệt chunk này"}
                  </button>
                </div>
              </>
            ) : <p className="muted">Chưa chọn chunk.</p>}
            </section>
          </SplitPdfReview>
        </div>
      ) : null}

      <div className="advancedActions">
        <button type="button" className="linkButton" onClick={() => setToolsOpen((value) => !value)}>
          {toolsOpen ? "Ẩn công cụ nâng cao" : "Công cụ nâng cao"}
        </button>
        {toolsOpen ? (
          <div className="advancedBox">
            <div className="actionBar">
              <button type="button" onClick={onSave} disabled={loading || approved || safeChunks.length === 0}>Lưu chỉnh sửa chunk</button>
              <button type="button" className="primaryButton" onClick={onApprove} disabled={loading || approved || safeChunks.length === 0}>Duyệt tất cả chunk</button>
              <button
                type="button"
                onClick={() => onApproveChunkIds?.((selectedGroup.chunks || []).map((chunk) => chunk.chunk_id || chunk.id).filter(Boolean))}
                disabled={loading || approved || !selectedGroup.chunks?.length}
              >
                Duyệt tất cả chunk trong bài này
              </button>
              {selectedChunk ? (
                <>
                  <button type="button" disabled={loading || approved || !onDelete} onClick={() => onDelete(selectedChunk.chunk_id || selectedChunk.id)}>Xóa chunk</button>
                  <button type="button" disabled={loading || approved || !onRecut} onClick={() => onRecut(selectedChunk)}>Cắt lại chunk</button>
                </>
              ) : null}
            </div>
            <form className="addChunkForm" onSubmit={submitAdd}>
              <h3>Thêm chunk</h3>
              <div className="addChunkGrid">
                <input placeholder="chunk_num" value={addForm.chunk_num} onChange={(event) => updateAdd("chunk_num", event.target.value)} />
                <input placeholder="chunk_name" value={addForm.chunk_name} onChange={(event) => updateAdd("chunk_name", event.target.value)} />
                <input placeholder="title" value={addForm.title} onChange={(event) => updateAdd("title", event.target.value)} />
                <input placeholder="heading" value={addForm.heading} onChange={(event) => updateAdd("heading", event.target.value)} />
                <input type="number" placeholder="start" value={addForm.start} onChange={(event) => updateAdd("start", event.target.value)} />
                <input type="number" placeholder="end" value={addForm.end} onChange={(event) => updateAdd("end", event.target.value)} />
                <label className="checkboxRow">
                  <input type="checkbox" checked={addForm.content_head} onChange={(event) => updateAdd("content_head", event.target.checked)} />
                  <span>content_head</span>
                </label>
              </div>
              <button type="submit" disabled={loading || approved || !onAdd}>Thêm chunk</button>
            </form>
          </div>
        ) : null}
      </div>

      <div className="wizardNav">
        <button type="button" onClick={onBack}>Quay lại</button>
        {approvedCount > 0 ? (
          <button type="button" className="primaryButton" onClick={onNext}>Tiếp tục hoàn tất</button>
        ) : safeChunks.length > 0 ? (
          <span className="inlineHelper">Bạn cần duyệt ít nhất một chunk trước khi sang bước hoàn tất.</span>
        ) : null}
      </div>
    </section>
  );
}

function friendlyChunkProgress(status) {
  const stage = String(status?.stage || "").toLowerCase();
  const message = String(status?.message || "");
  if (stage.includes("pdf") || stage.includes("split")) return "Đang cắt PDF theo chunk...";
  if (stage.includes("gemini") || message.toLowerCase().includes("gemini")) return "Đang trích xuất chunk bằng Gemini...";
  if (/key index|waiting_|gemini key/i.test(message)) return "Đang phân tích dữ liệu chunk...";
  return message || "Đang trích xuất chunk...";
}

function ProcessingState({ title, message, status }) {
  const rawPercent = Number(status?.percent);
  const hasPercent = Number.isFinite(rawPercent) && rawPercent > 0;
  const percent = Math.max(0, Math.min(rawPercent || 0, 100));
  return (
    <section className="processingState">
      <div className="processingIcon" aria-hidden="true" />
      <div>
        <h3>{title}</h3>
        <p>{message}</p>
        <strong>{friendlyChunkProgress(status)}</strong>
        <div className={`progressTrack ${hasPercent ? "" : "indeterminate"}`}>
          <div className="progressFill" style={{ width: hasPercent ? `${percent}%` : "42%" }} />
        </div>
        <small>Bạn có thể mở Debug để xem log chi tiết.</small>
      </div>
    </section>
  );
}
