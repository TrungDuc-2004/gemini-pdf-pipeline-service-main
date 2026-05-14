import { useEffect, useMemo, useState } from "react";
import { getSourcePreviewUrl, getTopicPreviewInfo, getTopicPreviewUrl } from "../api/reviewApi.js";
import EmptyState from "./EmptyState.jsx";
import ErrorState from "./ErrorState.jsx";
import LoadingState from "./LoadingState.jsx";
import SplitPdfReview from "./SplitPdfReview.jsx";

const ADVANCED_COLUMNS = ["topic_num", "topic_name", "start", "end", "raw_heading", "raw_title"];

function pad2(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? String(parsed).padStart(2, "0") : String(value || "--");
}

function topicStatus(topic) {
  if (topic?.metadata_edu_saved || topic?.minio_uploaded) return { label: "Đã lưu MinIO/MongoDB", tone: "done" };
  if (topic?.approved) return { label: "Đã duyệt", tone: "done" };
  if (topic?.error) return { label: "Lỗi", tone: "danger" };
  return { label: "Chờ duyệt", tone: "pending" };
}

export default function TopicReviewView({
  jobId,
  topics,
  approved,
  loading,
  status,
  jobStatus,
  error,
  onChange,
  onLoad,
  onExtract,
  onSave,
  onApproveAll,
  onApproveTopic,
  onExtractLessonsForTopic,
  onBack,
  onNext,
}) {
  const safeTopics = Array.isArray(topics) ? topics : [];
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [previewInfo, setPreviewInfo] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [previewVersion, setPreviewVersion] = useState(0);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [approving, setApproving] = useState(false);
  const [localMessage, setLocalMessage] = useState("");
  const [localError, setLocalError] = useState("");

  const approvedCount = safeTopics.filter((topic) => topic.approved || topic.metadata_edu_saved).length;
  const pendingCount = Math.max(safeTopics.length - approvedCount, 0);
  const isCooldown = jobStatus === "waiting_gemini_cooldown" || status?.status === "waiting_gemini_cooldown";
  const isExtracting = jobStatus === "extracting_topics" || status?.status === "extracting_topics" || isCooldown;
  const selectedTopic = safeTopics[Math.min(selectedIndex, Math.max(safeTopics.length - 1, 0))] || null;
  const selectedTopicNum = selectedTopic?.topic_num;
  const previewUrl = useMemo(() => {
    if (!jobId || !selectedTopicNum) return "";
    return `${getTopicPreviewUrl(jobId, selectedTopicNum)}?v=${previewVersion}`;
  }, [jobId, selectedTopicNum, previewVersion]);
  const extractedPreviewUrl = selectedTopic ? previewUrl : "";
  const sourcePreviewUrl = useMemo(() => (jobId ? getSourcePreviewUrl(jobId) : ""), [jobId]);

  useEffect(() => {
    if (selectedIndex > safeTopics.length - 1) setSelectedIndex(0);
  }, [safeTopics.length, selectedIndex]);

  useEffect(() => {
    let cancelled = false;
    async function loadPreview() {
      if (!jobId || !selectedTopicNum) {
        setPreviewInfo(null);
        setPreviewError("");
        return;
      }
      setPreviewLoading(true);
      setPreviewError("");
      try {
        const info = await getTopicPreviewInfo(jobId, selectedTopicNum);
        if (!cancelled) setPreviewInfo(info);
      } catch (err) {
        if (!cancelled) {
          setPreviewInfo(null);
          setPreviewError(err.message || "Không tải được preview topic.");
        }
      } finally {
        if (!cancelled) setPreviewLoading(false);
      }
    }
    loadPreview();
    return () => {
      cancelled = true;
    };
  }, [jobId, selectedTopicNum, previewVersion]);

  function updateSelected(field, value) {
    if (!selectedTopic) return;
    onChange(selectedIndex, field, value);
  }

  async function approveSelected() {
    if (!selectedTopic || !onApproveTopic) return;
    setApproving(true);
    setLocalError("");
    setLocalMessage(`Đang lưu Topic ${pad2(selectedTopic.topic_num)} lên MinIO/MongoDB...`);
    try {
      if (onSave) await onSave();
      const result = await onApproveTopic(selectedTopic);
      if (result === null) {
        throw new Error("Duyệt topic thất bại. Vui lòng xem thông báo lỗi hoặc nhật ký.");
      }
      setLocalMessage(`Đã duyệt Topic ${pad2(selectedTopic.topic_num)} và lưu lên MinIO/MongoDB.`);
      setPreviewVersion((value) => value + 1);
    } catch (err) {
      setLocalError(err.message || "Duyệt topic thất bại.");
    } finally {
      setApproving(false);
    }
  }

  return (
    <section className="panel reviewCard topicReviewWorkspace">
      <div className="topicReviewHeader">
        <div>
          <span className="stepLabel">Bước 2</span>
          <h2>Bước 2: Duyệt chủ đề</h2>
          <p className="muted">Chọn từng chủ đề, đối chiếu sách gốc với PDF đã cắt và lưu metadata.</p>
        </div>
        {safeTopics.length > 0 ? (
          <div className="topicSummaryChips" aria-label="Tổng quan chủ đề">
            <span>Tổng chủ đề: <strong>{safeTopics.length}</strong></span>
            <span>Đã duyệt: <strong>{approvedCount}/{safeTopics.length}</strong></span>
            <span>Chưa duyệt: <strong>{pendingCount}</strong></span>
          </div>
        ) : null}
        {safeTopics.length > 0 ? (
          <div className="topicHeaderActions">
            <button type="button" className="secondary-action" onClick={onExtract} disabled={loading || approving}>
              Trích xuất lại chủ đề
            </button>
            <button type="button" className="secondary-action" onClick={onLoad} disabled={loading || approving}>Tải lại</button>
          </div>
        ) : null}
      </div>

      {localMessage ? <div className="successBanner compactBanner">{localMessage}</div> : null}
      {localError ? <ErrorState message={localError} /> : null}
      {error ? <ErrorState message={error} onRetry={onLoad} /> : null}

      {isExtracting && safeTopics.length === 0 ? (
        <ProcessingState
          title={isCooldown ? "Gemini đang cooldown" : "Đang trích xuất chủ đề"}
          message={isCooldown ? "Chưa có dữ liệu chủ đề khả dụng. Hệ thống sẽ thử lại khi Gemini sẵn sàng." : "Hệ thống đang phân tích sách giáo khoa và chuẩn bị dữ liệu duyệt chủ đề."}
          detail={isCooldown ? "Nếu dữ liệu chủ đề đã tồn tại, bấm Tải lại hoặc kiểm tra Debug." : "Bạn có thể mở Debug nếu muốn xem log chi tiết."}
          status={status}
        />
      ) : null}

      {!isExtracting && safeTopics.length === 0 ? (
        <TopicEmptyOnboarding onExtract={onExtract} loading={loading || approving} />
      ) : null}

      {safeTopics.length > 0 ? (
        <>
          <div className="review-workspace topicReviewLayout">
            <TopicList topics={safeTopics} selectedIndex={selectedIndex} onSelect={setSelectedIndex} />
            <SplitPdfReview
              variant="topic"
              title="Sách gốc → PDF chủ đề"
              description="Sách giáo khoa gốc ở bên trái, kết quả trích xuất của topic đang chọn ở bên phải."
              sourcePreviewUrl={sourcePreviewUrl}
              extractedPreviewUrl={extractedPreviewUrl}
              sourceLabel="Sách giáo khoa gốc"
              extractedLabel="Topic đã trích xuất"
              sourcePageHint="Bản PDF nguồn của phiên duyệt"
              extractedPageHint={selectedTopic ? `Topic ${pad2(selectedTopic.topic_num)} · Trang ${selectedTopic.start || "-"}-${selectedTopic.end || "-"}` : ""}
              extractedStatusBadge={<span className={`status-chip ${topicStatus(selectedTopic).tone}`}>{topicStatus(selectedTopic).label}</span>}
              missingExtractedMessage={
                selectedTopic
                  ? "Chưa có PDF chủ đề"
                  : previewError || "Chưa có PDF chủ đề"
              }
              missingExtractedDetail={
                previewInfo?.topics_extracted === false
                  ? "Hãy trích xuất chủ đề trước. Nếu đã trích xuất, kiểm tra Debug để xem đường dẫn preview."
                  : "Vẫn có thể duyệt metadata topic này. Kiểm tra Debug nếu cần xem đường dẫn preview."
              }
            />
            <div className="review-metadata-panel topicMetadataRail">
              {previewLoading ? <LoadingState message="Đang tải thông tin preview..." /> : null}
              <TopicEditorCard
                topic={selectedTopic}
                loading={loading || approving}
                onUpdate={updateSelected}
                onSave={onSave}
                onApprove={approveSelected}
                onExtractLessons={onExtractLessonsForTopic}
              />
            </div>
          </div>
        </>
      ) : null}

      {safeTopics.length > 0 ? (
        <>
          <div className="advancedActions subtleAdvanced">
            <button type="button" className="linkButton" onClick={() => setBulkOpen((value) => !value)}>
              {bulkOpen ? "Ẩn nâng cao" : "Nâng cao"}
            </button>
            {bulkOpen ? (
              <div className="advancedBox">
                <p className="muted">Các thao tác hàng loạt chỉ dùng khi cần rà soát nhanh dữ liệu đã trích xuất.</p>
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm("Thao tác này sẽ lưu toàn bộ topic lên MongoDB/MinIO. Tiếp tục?")) onApproveAll?.();
                  }}
                  disabled={loading || approving || approved}
                >
                  Duyệt tất cả topic
                </button>
                <button type="button" className="linkButton" onClick={() => setAdvancedOpen((value) => !value)}>
                  {advancedOpen ? "Ẩn chỉnh sửa nâng cao" : "Chỉnh sửa nâng cao"}
                </button>
              </div>
            ) : null}
          </div>
          {advancedOpen ? <AdvancedTopicTable topics={safeTopics} onChange={onChange} disabled={loading || approving} /> : null}
        </>
      ) : null}

      <div className="wizardNav">
        <button type="button" onClick={onBack}>Quay lại</button>
        {safeTopics.length > 0 && approvedCount > 0 ? (
          <button type="button" className="primaryButton" onClick={onNext}>Sang bài học</button>
        ) : safeTopics.length > 0 ? (
          <span className="inlineHelper">Bạn cần duyệt ít nhất một chủ đề trước khi sang bước bài học.</span>
        ) : null}
      </div>
    </section>
  );
}

function friendlyTopicProgress(status) {
  const stage = String(status?.stage || "").toLowerCase();
  const message = String(status?.message || "");
  if (stage.includes("split") || message.toLowerCase().includes("pdf")) return "Đang cắt PDF theo chủ đề...";
  if (stage.includes("gemini") || message.toLowerCase().includes("gemini")) return "Đang trích xuất danh sách chủ đề...";
  if (/key index|waiting_|gemini key/i.test(message)) return "Đang phân tích sách bằng Gemini...";
  return message || "Đang chuẩn bị dữ liệu duyệt...";
}

function ProcessingState({ title, message, detail, status }) {
  const rawPercent = Number(status?.percent);
  const hasPercent = Number.isFinite(rawPercent) && rawPercent > 0;
  const percent = Math.max(0, Math.min(rawPercent || 0, 100));
  return (
    <section className="processingState">
      <div className="processingIcon" aria-hidden="true" />
      <div>
        <h3>{title}</h3>
        <p>{message}</p>
        <strong>{friendlyTopicProgress(status)}</strong>
        <div className={`progressTrack ${hasPercent ? "" : "indeterminate"}`}>
          <div className="progressFill" style={{ width: hasPercent ? `${percent}%` : "42%" }} />
        </div>
        <ol className="processList">
          <li className="done">Tải sách lên MinIO</li>
          <li className="active">Gọi Gemini phân tích cấu trúc</li>
          <li>Cắt PDF chủ đề</li>
          <li>Chuẩn bị màn hình duyệt</li>
        </ol>
        <small>{detail}</small>
      </div>
    </section>
  );
}

function TopicEmptyOnboarding({ onExtract, loading }) {
  return (
    <section className="topicOnboarding">
      <div className="onboardingVisual" aria-hidden="true">
        <span>PDF</span>
        <i />
        <strong>AI</strong>
        <i />
        <span>Review</span>
      </div>
      <div className="onboardingCopy">
        <span className="stepLabel">Bắt đầu duyệt</span>
        <h3>Chưa có dữ liệu chủ đề</h3>
        <p>Hệ thống cần trích xuất danh sách chủ đề từ sách giáo khoa trước khi duyệt.</p>
        <div className="onboardingSteps">
          <div><strong>1</strong><span>Gọi Gemini phân tích sách</span></div>
          <div><strong>2</strong><span>Cắt PDF theo từng chủ đề</span></div>
          <div><strong>3</strong><span>Hiển thị preview để duyệt</span></div>
        </div>
        <button type="button" className="primaryButton primary-action" onClick={onExtract} disabled={loading}>Trích xuất chủ đề</button>
        <small>Sau khi trích xuất, bạn sẽ thấy danh sách chủ đề và preview PDF để đối chiếu.</small>
      </div>
    </section>
  );
}

function TopicList({ topics, selectedIndex, onSelect }) {
  return (
    <nav className="review-navigator compact-topic-nav">
      <div className="cardHeaderCompact">
        <h3>Danh sách chủ đề</h3>
        <span>{topics.length} topic</span>
      </div>
      <div className="compactNavScroller">
        {topics.map((topic, index) => {
          const status = topicStatus(topic);
          return (
            <button
              type="button"
              className={`review-list-item compact-nav-item ${index === selectedIndex ? "review-list-item-active active" : ""}`}
              key={topic.name || `${topic.topic_num}-${index}`}
              onClick={() => onSelect(index)}
            >
              <div>
                <strong>Topic {pad2(topic.topic_num)}</strong>
                <span>{topic.topic_name || "-"}</span>
                <small>Trang {topic.start || "-"}-{topic.end || "-"}</small>
              </div>
              <em className={`inlineStatus ${status.tone}`}>{status.label}</em>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

function TopicEditorCard({ topic, loading, onUpdate, onSave, onApprove, onExtractLessons }) {
  if (!topic) return null;
  const isApproved = Boolean(topic.approved || topic.metadata_edu_saved || topic.minio_uploaded);
  return (
    <section className="topicEditorCard review-editor">
      <div>
        <h3>Metadata chủ đề</h3>
        <p className="muted">Kiểm tra và chỉnh sửa thông tin trước khi lưu.</p>
      </div>
      <div className="topicEditorGrid">
        <label>
          <span>Số topic</span>
          <input type="number" value={topic.topic_num ?? ""} disabled={isApproved} onChange={(event) => onUpdate("topic_num", Number(event.target.value))} />
        </label>
        <label className="wide">
          <span>Tên chủ đề</span>
          <input type="text" value={topic.topic_name ?? ""} disabled={isApproved} onChange={(event) => onUpdate("topic_name", event.target.value)} />
        </label>
        <label>
          <span>Trang bắt đầu</span>
          <input type="number" value={topic.start ?? ""} disabled={isApproved} onChange={(event) => onUpdate("start", Number(event.target.value))} />
        </label>
        <label>
          <span>Trang kết thúc</span>
          <input type="number" value={topic.end ?? ""} disabled={isApproved} onChange={(event) => onUpdate("end", Number(event.target.value))} />
        </label>
      </div>
      {isApproved ? (
        <div className="successBox">Topic này đã được lưu vào MongoDB và MinIO.</div>
      ) : null}
      <div className="approvalActions">
        <button type="button" className="secondary-action" onClick={onSave} disabled={loading || isApproved}>Lưu chỉnh sửa</button>
        <button type="button" className={isApproved ? "secondary-action approvedAction" : "primaryButton primary-action"} onClick={onApprove} disabled={loading || isApproved}>
          {isApproved ? "Đã duyệt topic" : "Duyệt topic này"}
        </button>
        {isApproved ? (
          <button type="button" className="primaryButton primary-action nextTopicAction" onClick={() => onExtractLessons?.(topic)} disabled={loading}>
            Trích xuất bài học topic này
          </button>
        ) : null}
      </div>
    </section>
  );
}

function AdvancedTopicTable({ topics, onChange, disabled }) {
  return (
    <div className="tableWrap advancedTopicTableWrap">
      <table className="reviewTable topicReviewTable">
        <thead>
          <tr>
            <th>#</th>
            {ADVANCED_COLUMNS.map((column) => <th key={column}>{column}</th>)}
          </tr>
        </thead>
        <tbody>
          {topics.map((topic, index) => (
            <tr key={topic.name || `${topic.topic_num}-${index}`}>
              <td>{index + 1}</td>
              {ADVANCED_COLUMNS.map((column) => (
                <td key={column}>
                  <input
                    type={column === "start" || column === "end" || column === "topic_num" ? "number" : "text"}
                    value={topic[column] ?? ""}
                    disabled={disabled || topic.approved}
                    onChange={(event) => onChange(index, column, column === "start" || column === "end" || column === "topic_num" ? Number(event.target.value) : event.target.value)}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
