import { useMemo, useState } from "react";
import { getLessonPreviewUrl, getSourcePreviewUrl } from "../api/reviewApi.js";
import EmptyState from "./EmptyState.jsx";
import ErrorState from "./ErrorState.jsx";
import LoadingState from "./LoadingState.jsx";
import SplitPdfReview from "./SplitPdfReview.jsx";

function groupsFrom(lessons, groupedByTopic) {
  if (Array.isArray(groupedByTopic) && groupedByTopic.length) return groupedByTopic;
  const map = new Map();
  for (const lesson of Array.isArray(lessons) ? lessons : []) {
    const key = `${lesson.topic_num || ""}-${lesson.topic_name || ""}`;
    if (!map.has(key)) map.set(key, { topic_num: lesson.topic_num, topic_name: lesson.topic_name, lessons: [] });
    map.get(key).lessons.push(lesson);
  }
  return Array.from(map.values());
}

function pad2(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? String(parsed).padStart(2, "0") : String(value || "--");
}

function lessonStatus(lesson) {
  if (lesson?.metadata_edu_saved || lesson?.minio_uploaded) return { label: "Đã lưu MinIO/MongoDB", tone: "done" };
  if (lesson?.approved) return { label: "Đã duyệt", tone: "pending" };
  return { label: "Chờ duyệt", tone: "pending" };
}

export default function LessonReviewView({
  jobId,
  lessons,
  groupedByTopic,
  selectedTopicNum,
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
  onApproveLesson,
  onExtractChunksForLesson,
  onBack,
  onNext,
}) {
  const safeLessons = Array.isArray(lessons) ? lessons : [];
  const groups = useMemo(() => groupsFrom(safeLessons, groupedByTopic), [safeLessons, groupedByTopic]);
  const [selectedGroupIndex, setSelectedGroupIndex] = useState(0);
  const [selectedLessonName, setSelectedLessonName] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const selectedGroup = groups[Math.min(selectedGroupIndex, Math.max(groups.length - 1, 0))] || { lessons: [] };
  const selectedLesson = selectedGroup.lessons?.find((lesson) => lesson.name === selectedLessonName) || selectedGroup.lessons?.[0] || safeLessons[0];
  const selectedLessonIndex = selectedLesson ? safeLessons.findIndex((item) => item === selectedLesson || item.name === selectedLesson.name) : -1;
  const approvedCount = safeLessons.filter((lesson) => lesson.metadata_edu_saved || lesson.approved).length;
  const sourcePreviewUrl = jobId ? getSourcePreviewUrl(jobId) : "";
  const lessonPreviewUrl = jobId && selectedLesson?.lesson_num ? getLessonPreviewUrl(jobId, selectedLesson.lesson_num) : "";
  const isExtracting = jobStatus === "extracting_lessons" || status?.status === "extracting_lessons";

  function updateSelected(field, value) {
    if (selectedLessonIndex >= 0) onChange(selectedLessonIndex, field, value);
  }

  return (
    <section className="panel reviewCard lessonReviewWorkspace">
      <div className="topicReviewHeader">
        <div>
          <span className="stepLabel">Bước 3</span>
          <h2>{selectedTopicNum ? `Bước 3: Bài học của Topic ${pad2(selectedTopicNum)}` : "Bước 3: Duyệt bài học"}</h2>
          <p className="muted">Đối chiếu sách gốc với PDF bài học đã cắt và lưu metadata bài học.</p>
        </div>
        <div className="topicHeaderActions">
          <button type="button" onClick={onExtract} disabled={loading}>Trích xuất bài học</button>
          <button type="button" className="secondary-action" onClick={onLoad} disabled={loading}>Tải lại</button>
        </div>
      </div>

      {safeLessons.length > 0 ? (
        <div className="summaryCards">
          <div className="summaryCard"><span>Tổng bài học</span><strong>{safeLessons.length}</strong></div>
          <div className="summaryCard"><span>Đã duyệt</span><strong>{approvedCount}</strong></div>
          <div className="summaryCard"><span>Chưa duyệt</span><strong>{Math.max(safeLessons.length - approvedCount, 0)}</strong></div>
        </div>
      ) : null}

      {loading && !isExtracting ? <LoadingState message="Đang tải bài học..." /> : null}
      {error ? <ErrorState message={error} onRetry={onLoad} /> : null}
      {isExtracting && safeLessons.length === 0 ? (
        <ProcessingState
          title="Đang trích xuất bài học"
          message="Hệ thống đang phân tích các chủ đề đã duyệt để tạo danh sách bài học và PDF tương ứng."
          status={status}
        />
      ) : null}
      {!isExtracting && !loading && !error && lessons == null ? <EmptyState message="Chưa có dữ liệu bài học. Hãy duyệt chủ đề trước khi trích xuất bài học." /> : null}
      {!isExtracting && !loading && !error && lessons != null && safeLessons.length === 0 ? <EmptyState message="Danh sách bài học đang trống." /> : null}

      {groups.length > 0 ? (
        <div className="review-workspace lessonReviewLayout">
          <aside className="review-navigator">
            <div className="cardHeaderCompact">
              <h3>Danh sách bài học</h3>
              <span>{groups.length} topic</span>
            </div>
            {groups.map((group, groupIndex) => (
              <div className="navigatorGroup" key={`${group.topic_num}-${group.topic_name}-${groupIndex}`}>
                <div className="navigatorGroupTitle">
                  <strong>{group.topic_num ? `Topic ${pad2(group.topic_num)}` : "Topic"}</strong>
                  <span>{group.lessons?.length || 0} bài học</span>
                </div>
                {(group.lessons || []).map((lesson, localIndex) => {
                  const status = lessonStatus(lesson);
                  const active = selectedGroupIndex === groupIndex && (selectedLesson?.name === lesson.name || (!selectedLessonName && localIndex === 0));
                  return (
                    <button
                      type="button"
                      key={lesson.name || `${groupIndex}-${localIndex}`}
                      className={`review-list-item ${active ? "review-list-item-active" : ""}`}
                      onClick={() => {
                        setSelectedGroupIndex(groupIndex);
                        setSelectedLessonName(lesson.name);
                      }}
                    >
                      <strong>Lesson {pad2(lesson.lesson_num)}</strong>
                      <span>{lesson.lesson_name || "-"}</span>
                      <small>Trang {lesson.start || "-"}-{lesson.end || "-"}</small>
                      <em className={`status-chip ${status.tone}`}>{status.label}</em>
                    </button>
                  );
                })}
              </div>
            ))}
          </aside>

          <SplitPdfReview
            title="Đối chiếu bản gốc và bản cắt"
            description="Sách giáo khoa gốc ở bên trái, bài học đã trích xuất ở bên phải."
            sourcePreviewUrl={sourcePreviewUrl}
            extractedPreviewUrl={lessonPreviewUrl}
            sourceLabel="Sách giáo khoa gốc"
            extractedLabel="Bài học đã trích xuất"
            sourcePageHint="Bản PDF nguồn của phiên duyệt"
            extractedPageHint={selectedLesson ? `Lesson ${pad2(selectedLesson.lesson_num)} · Trang ${selectedLesson.start || "-"}-${selectedLesson.end || "-"}` : ""}
            extractedStatusBadge={selectedLesson ? <span className={`status-chip ${lessonStatus(selectedLesson).tone}`}>{lessonStatus(selectedLesson).label}</span> : null}
            missingExtractedMessage="Preview bài học chưa sẵn sàng."
            missingExtractedDetail="Nếu bài học vừa được trích xuất, vui lòng chờ thêm hoặc mở Debug để kiểm tra log."
          >
            <section className="review-editor">
              <h3>Metadata bài học</h3>
            {selectedLesson ? (
              <>
                <div className="metadataForm">
                  <label>
                    <span>Số bài</span>
                    <input type="number" value={selectedLesson.lesson_num ?? ""} disabled={approved} onChange={(event) => updateSelected("lesson_num", Number(event.target.value))} />
                  </label>
                  <label>
                    <span>Tên bài học</span>
                    <input value={selectedLesson.lesson_name ?? ""} disabled={approved} onChange={(event) => updateSelected("lesson_name", event.target.value)} />
                  </label>
                  <label>
                    <span>Loại bài</span>
                    <input value={selectedLesson.lesson_type ?? ""} disabled={approved} onChange={(event) => updateSelected("lesson_type", event.target.value)} />
                  </label>
                  <div className="twoColumn">
                    <label>
                      <span>Trang bắt đầu</span>
                      <input type="number" value={selectedLesson.start ?? ""} disabled={approved} onChange={(event) => updateSelected("start", Number(event.target.value))} />
                    </label>
                    <label>
                      <span>Trang kết thúc</span>
                      <input type="number" value={selectedLesson.end ?? ""} disabled={approved} onChange={(event) => updateSelected("end", Number(event.target.value))} />
                    </label>
                  </div>
                </div>
                {selectedLesson.metadata_edu_saved ? <div className="successBox">Đã lưu bài học vào MongoDB và MinIO.</div> : null}
                <div className="approvalActions">
                  <button type="button" className="secondary-action" onClick={onSave} disabled={loading || approved}>Lưu chỉnh sửa</button>
                  <button type="button" className="primaryButton primary-action" onClick={() => onApproveLesson?.(selectedLesson)} disabled={loading || selectedLesson.metadata_edu_saved}>
                    Duyệt bài học này
                  </button>
                  {selectedLesson.metadata_edu_saved ? (
                    <button type="button" className="primaryButton" onClick={() => onExtractChunksForLesson?.(selectedLesson)} disabled={loading}>
                      Trích xuất chunk bài học này
                    </button>
                  ) : null}
                </div>
                <details className="advancedBox">
                  <summary>Chỉnh sửa nâng cao</summary>
                  <dl className="statusGrid">
                    <dt>Topic</dt><dd>{selectedLesson.topic_num || "-"}</dd>
                    <dt>name</dt><dd className="mono breakText">{selectedLesson.name || "-"}</dd>
                  </dl>
                </details>
              </>
            ) : <p className="muted">Chưa chọn bài học.</p>}
            </section>
          </SplitPdfReview>
        </div>
      ) : null}

      <div className="advancedActions">
        <button type="button" className="linkButton" onClick={() => setAdvancedOpen((value) => !value)}>
          {advancedOpen ? "Ẩn thao tác nâng cao" : "Thao tác nâng cao"}
        </button>
        {advancedOpen ? (
          <div className="advancedBox">
            <button type="button" onClick={onApprove} disabled={loading || approved || safeLessons.length === 0}>Duyệt tất cả bài học</button>
          </div>
        ) : null}
      </div>

      <div className="wizardNav">
        <button type="button" onClick={onBack}>Quay lại</button>
        {approvedCount > 0 ? (
          <button type="button" className="primaryButton" onClick={onNext}>Tiếp tục chunk</button>
        ) : safeLessons.length > 0 ? (
          <span className="inlineHelper">Bạn cần duyệt ít nhất một bài học trước khi sang bước chunk.</span>
        ) : null}
      </div>
    </section>
  );
}

function friendlyLessonProgress(status) {
  const stage = String(status?.stage || "").toLowerCase();
  const message = String(status?.message || "");
  if (stage.includes("pdf") || stage.includes("split")) return "Đang cắt PDF theo bài học...";
  if (stage.includes("gemini") || message.toLowerCase().includes("gemini")) return "Đang trích xuất bài học bằng Gemini...";
  if (/key index|waiting_|gemini key/i.test(message)) return "Đang phân tích dữ liệu bài học...";
  return message || "Đang trích xuất bài học...";
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
        <strong>{friendlyLessonProgress(status)}</strong>
        <div className={`progressTrack ${hasPercent ? "" : "indeterminate"}`}>
          <div className="progressFill" style={{ width: hasPercent ? `${percent}%` : "42%" }} />
        </div>
        <small>Bạn có thể mở Debug để xem log chi tiết.</small>
      </div>
    </section>
  );
}
