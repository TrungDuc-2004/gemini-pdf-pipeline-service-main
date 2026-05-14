import { useEffect, useRef, useState } from "react";
import {
  API_BASE_URL,
  addChunk,
  approveChunk,
  approveChunkIds,
  approveChunks,
  approveLesson,
  approveLessons,
  approveTopic,
  approveTopics,
  deleteChunk,
  downloadBundle,
  extractChunks,
  extractChunksForLesson,
  extractLessons,
  extractLessonsForTopic,
  extractTopics,
  finalizeChunksAfterKaggle,
  getBundle,
  getChunks,
  getJob,
  getLessons,
  getLogs,
  getMongoImportResult,
  getSourcePreviewUrl,
  getStatus,
  getTopics,
  health,
  importMongo,
  itemsFromResponse,
  listJobs,
  prepareBundle,
  recutChunk,
  retryGeminiStage,
  saveChunks,
  saveLessons,
  saveTopics,
} from "./api/reviewApi.js";
import BookUploadForm from "./components/BookUploadForm.jsx";
import ChunkReviewView from "./components/ChunkReviewView.jsx";
import EmptyState from "./components/EmptyState.jsx";
import ErrorState from "./components/ErrorState.jsx";
import JobList from "./components/JobList.jsx";
import JobStatusBadge from "./components/JobStatusBadge.jsx";
import LessonReviewView from "./components/LessonReviewView.jsx";
import LoadingState from "./components/LoadingState.jsx";
import LogPanel from "./components/LogPanel.jsx";
import RawJsonPanel from "./components/RawJsonPanel.jsx";
import ReviewStepper from "./components/ReviewStepper.jsx";
import StatusPanel from "./components/StatusPanel.jsx";
import TopicReviewView from "./components/TopicReviewView.jsx";
import BundleView from "./views/BundleView.jsx";

const WORKFLOW_STEPS = { upload: "upload", topics: "topics", lessons: "lessons", chunks: "chunks", bundle: "bundle" };
const BUSY_STATUSES = new Set([
  "extracting_topics",
  "extracting_lessons",
  "extracting_chunks",
  "preparing_bundle",
  "running_kaggle",
  "extracting_keywords",
  "importing_mongodb",
]);

function friendlyProgress(status, fallback = "Đang xử lý dữ liệu...") {
  const stage = String(status?.stage || "").toLowerCase();
  const message = String(status?.message || "");
  if (stage.includes("topic") || status?.status === "extracting_topics") {
    if (stage.includes("split") || message.toLowerCase().includes("pdf")) return "Đang cắt PDF theo chủ đề...";
    if (stage.includes("gemini") || message.toLowerCase().includes("gemini")) return "Đang trích xuất danh sách chủ đề...";
    return "Đang phân tích sách bằng Gemini...";
  }
  if (stage.includes("lesson") || status?.status === "extracting_lessons") return "Đang trích xuất danh sách bài học...";
  if (stage.includes("chunk") || status?.status === "extracting_chunks") return "Đang trích xuất chunk bằng Gemini...";
  if (stage.includes("bundle") || status?.status === "preparing_bundle") return "Đang chuẩn bị dữ liệu hoàn tất...";
  if (stage.includes("kaggle") || status?.status === "running_kaggle") return "Đang xử lý Kaggle OCR/cutline...";
  if (stage.includes("keyword") || status?.status === "extracting_keywords") return "Đang trích xuất keyword...";
  if (stage.includes("mongo") || status?.status === "importing_mongodb") return "Đang lưu metadata vào MongoDB...";
  if (/key index|waiting_|gemini key/i.test(message)) return fallback;
  return message || fallback;
}

export default function App() {
  const [healthInfo, setHealthInfo] = useState(null);
  const [healthError, setHealthError] = useState("");
  const [jobs, setJobs] = useState([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [jobsError, setJobsError] = useState("");
  const [selectedJobId, setSelectedJobId] = useState("");
  const [job, setJob] = useState(null);
  const [status, setStatus] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState("");
  const [navWarning, setNavWarning] = useState("");
  const [activeStep, setActiveStep] = useState(WORKFLOW_STEPS.upload);
  const [topics, setTopics] = useState(null);
  const [topicsApproved, setTopicsApproved] = useState(false);
  const [topicsError, setTopicsError] = useState("");
  const [selectedTopicNum, setSelectedTopicNum] = useState("");
  const [lessons, setLessons] = useState(null);
  const [groupedLessons, setGroupedLessons] = useState([]);
  const [lessonsApproved, setLessonsApproved] = useState(false);
  const [lessonsError, setLessonsError] = useState("");
  const [selectedLessonNum, setSelectedLessonNum] = useState("");
  const [chunks, setChunks] = useState(null);
  const [groupedChunks, setGroupedChunks] = useState([]);
  const [chunksApproved, setChunksApproved] = useState(false);
  const [chunksError, setChunksError] = useState("");
  const [actionLoading, setActionLoading] = useState(false);
  const [successMessage, setSuccessMessage] = useState("");
  const [bundleResult, setBundleResult] = useState(null);
  const [mongoResult, setMongoResult] = useState(null);
  const [logs, setLogs] = useState("");
  const [rawOpen, setRawOpen] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const pollRef = useRef(null);
  const topicAutoLoadRef = useRef("");

  useEffect(() => {
    checkHealth();
    loadJobs();
    return stopPolling;
  }, []);

  useEffect(() => {
    if (selectedJobId) {
      loadSelectedJob(selectedJobId);
    }
  }, [selectedJobId]);

  useEffect(() => {
    if (!selectedJobId || activeStep !== WORKFLOW_STEPS.topics || detailsLoading) return;
    const hasLoadedTopics = Array.isArray(topics);
    const shouldReloadKnownTopics = Boolean(status?.can_review_topics || job?.can_review_topics) && (!hasLoadedTopics || topics.length === 0);
    if (hasLoadedTopics && !shouldReloadKnownTopics) return;
    const topicCountHint = status?.topic_count ?? job?.topic_count ?? "unknown";
    const autoLoadKey = `${selectedJobId}:${job?.status || status?.status || "unknown"}:${topicCountHint}`;
    if (topicAutoLoadRef.current === autoLoadKey) return;
    topicAutoLoadRef.current = autoLoadKey;
    loadTopics({ silent: true });
  }, [
    selectedJobId,
    activeStep,
    detailsLoading,
    job?.status,
    status?.status,
    status?.can_review_topics,
    status?.topic_count,
    job?.can_review_topics,
    job?.topic_count,
    topics,
  ]);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function startPolling() {
    stopPolling();
    pollRef.current = setInterval(async () => {
      if (!selectedJobId) return;
      try {
        const nextStatus = await getStatus(selectedJobId);
        const nextJob = await getJob(selectedJobId);
        setStatus(nextStatus);
        setJob(nextJob);
        await loadJobs(false);
        if (!BUSY_STATUSES.has(nextStatus?.status)) stopPolling();
      } catch (err) {
        setDetailsError(err.message);
        stopPolling();
      }
    }, 2000);
  }

  function inferStepFromStatus(value, stageValue = "") {
    const stage = String(stageValue || "").toLowerCase();
    if (value === "waiting_gemini_cooldown") {
      if (stage.includes("lesson")) return WORKFLOW_STEPS.lessons;
      if (stage.includes("chunk")) return WORKFLOW_STEPS.chunks;
      return WORKFLOW_STEPS.topics;
    }
    if (value === "uploaded") return WORKFLOW_STEPS.topics;
    if (value === "extracting_topics" || value === "reviewing_topics") return WORKFLOW_STEPS.topics;
    if (value === "extracting_lessons" || value === "reviewing_lessons") return WORKFLOW_STEPS.lessons;
    if (value === "extracting_chunks" || value === "reviewing_chunks") return WORKFLOW_STEPS.chunks;
    if (
      value === "preparing_bundle" ||
      value === "running_kaggle" ||
      value === "extracting_keywords" ||
      value === "bundle_ready" ||
      value === "importing_mongodb" ||
      value === "mongodb_imported"
    ) {
      return WORKFLOW_STEPS.bundle;
    }
    if (value === "error") return activeStep === WORKFLOW_STEPS.upload ? WORKFLOW_STEPS.topics : activeStep;
    return WORKFLOW_STEPS.upload;
  }

  async function checkHealth() {
    try {
      const data = await health();
      setHealthInfo(data);
      setHealthError("");
    } catch (err) {
      setHealthInfo(null);
      setHealthError(err.message || "Không kết nối được backend");
    }
  }

  async function loadJobs(showLoading = true) {
    if (showLoading) setJobsLoading(true);
    setJobsError("");
    try {
      const response = await listJobs();
      const nextJobs = itemsFromResponse(response, "items");
      setJobs(nextJobs);
    } catch (err) {
      setJobs([]);
      setJobsError(`Không tải được danh sách sách/job. Kiểm tra backend tại ${API_BASE_URL}. ${err.message}`);
    } finally {
      if (showLoading) setJobsLoading(false);
    }
  }

  async function loadSelectedJob(jobId, options = {}) {
    setDetailsLoading(true);
    setDetailsError("");
    setNavWarning("");
    if (!options.keepMessage) setSuccessMessage("");
    if (!options.keepResults) {
      setBundleResult(null);
      setMongoResult(null);
    }
    try {
      const [jobData, statusData, logsData] = await Promise.all([getJob(jobId), getStatus(jobId), getLogs(jobId, 200).catch(() => null)]);
      setJob(jobData);
      setStatus(statusData);
      if (!options.keepReview) resetReviewData();
      if (!options.keepStep) setActiveStep(inferStepFromStatus(jobData?.status || statusData?.status, jobData?.stage || statusData?.stage));
      if (logsData?.log) setLogs(logsData.log);
      if (BUSY_STATUSES.has(statusData?.status)) startPolling();
    } catch (err) {
      setJob(null);
      setStatus(null);
      setDetailsError(err.message);
    } finally {
      setDetailsLoading(false);
    }
  }

  function resetReviewData() {
    topicAutoLoadRef.current = "";
    setTopics(null);
    setTopicsApproved(false);
    setTopicsError("");
    setSelectedTopicNum("");
    setLessons(null);
    setGroupedLessons([]);
    setLessonsApproved(false);
    setLessonsError("");
    setSelectedLessonNum("");
    setChunks(null);
    setGroupedChunks([]);
    setChunksApproved(false);
    setChunksError("");
  }

  async function afterUpload(created) {
    const bucket = created?.minio?.bucket || "ai-tra-cuu";
    setSuccessMessage(`Sách đã được tải lên MinIO bucket ${bucket}. Đang chờ trích xuất chủ đề.`);
    setActiveStep(WORKFLOW_STEPS.topics);
    await loadJobs(false);
    if (created?.job_id) setSelectedJobId(created.job_id);
  }

  function updateItem(setter, index, field, value) {
    setter((current) => (Array.isArray(current) ? current : []).map((item, itemIndex) => (itemIndex === index ? { ...item, [field]: value } : item)));
  }

  async function runAction(action, options = {}) {
    const { success, reload } = options;
    if (!selectedJobId) return;
    setActionLoading(true);
    setDetailsError("");
    setNavWarning("");
    setSuccessMessage(options.loadingMessage || "");
    try {
      const result = await action();
      if (success) setSuccessMessage(success);
      await Promise.all([loadJobs(false), loadSelectedJob(selectedJobId, { keepMessage: true, keepReview: true, keepResults: true, keepStep: true })]);
      if (options.poll !== false && (BUSY_STATUSES.has(result?.status) || BUSY_STATUSES.has(status?.status))) startPolling();
      if (reload) await reload(result);
      return result;
    } catch (err) {
      options.onError?.(err.message);
      if (!options.onError) setDetailsError(err.message);
      return null;
    } finally {
      setActionLoading(false);
    }
  }

  async function loadTopics(options = {}) {
    if (!selectedJobId) return;
    setTopicsError("");
    if (!options.silent) setActionLoading(true);
    try {
      const response = await getTopics(selectedJobId);
      setTopics(Array.isArray(response?.topics) ? response.topics : itemsFromResponse(response, "topics"));
      setTopicsApproved(Boolean(response?.approved));
      setActiveStep(WORKFLOW_STEPS.topics);
    } catch (err) {
      if (!options.silent) setTopics(null);
      setTopicsError(err.message);
    } finally {
      if (!options.silent) setActionLoading(false);
    }
  }

  async function loadLessons() {
    if (!selectedJobId) return;
    setLessonsError("");
    setActionLoading(true);
    try {
      const response = await getLessons(selectedJobId);
      setLessons(itemsFromResponse(response, "lessons"));
      setGroupedLessons(Array.isArray(response?.grouped_by_topic) ? response.grouped_by_topic : []);
      setLessonsApproved(Boolean(response?.approved));
      setActiveStep(WORKFLOW_STEPS.lessons);
    } catch (err) {
      setLessons(null);
      setLessonsError(err.message);
    } finally {
      setActionLoading(false);
    }
  }

  async function loadChunks() {
    if (!selectedJobId) return;
    setChunksError("");
    setActionLoading(true);
    try {
      const response = await getChunks(selectedJobId);
      setChunks(itemsFromResponse(response, "chunks"));
      setGroupedChunks(Array.isArray(response?.grouped_by_lesson) ? response.grouped_by_lesson : []);
      setChunksApproved(Boolean(response?.approved));
      setActiveStep(WORKFLOW_STEPS.chunks);
    } catch (err) {
      setChunks(null);
      setChunksError(err.message);
    } finally {
      setActionLoading(false);
    }
  }

  async function runHeavyStage() {
    if (!selectedJobId) return;
    setActionLoading(true);
    setDetailsError("");
    setSuccessMessage("");
    try {
      let result;
      if (status?.status === "bundle_ready" || status?.status === "mongodb_imported") {
        result = await importMongo(selectedJobId);
        setMongoResult(result);
        setSuccessMessage("Đã chạy import MongoDB.");
      } else {
        result = await prepareBundle(selectedJobId);
        setSuccessMessage("Đã bắt đầu chuẩn bị bundle/heavy stage.");
        startPolling();
      }
      await Promise.all([loadJobs(false), loadSelectedJob(selectedJobId, { keepMessage: true, keepReview: true, keepResults: true, keepStep: true })]);
      return result;
    } catch (err) {
      setDetailsError(err.message);
      try {
        const previous = await getMongoImportResult(selectedJobId);
        setMongoResult(previous);
      } catch {
        // No previous import result available.
      }
      return null;
    } finally {
      setActionLoading(false);
    }
  }

  async function refreshBundleResult() {
    if (!selectedJobId) return;
    setActionLoading(true);
    try {
      const result = status?.status === "mongodb_imported" ? await getMongoImportResult(selectedJobId) : await getBundle(selectedJobId);
      if (status?.status === "mongodb_imported") setMongoResult(result);
      else setBundleResult(result);
    } catch (err) {
      setDetailsError(err.message);
    } finally {
      setActionLoading(false);
    }
  }

  async function loadLogs() {
    if (!selectedJobId) return;
    setActionLoading(true);
    try {
      const response = await getLogs(selectedJobId, 300);
      setLogs(response?.log || "");
    } catch (err) {
      setDetailsError(err.message);
    } finally {
      setActionLoading(false);
    }
  }

  async function prepareBundleAction(options = {}) {
    return runAction(() => prepareBundle(selectedJobId, options), {
      success: "Đã bắt đầu chuẩn bị bundle.",
      reload: async () => {
        setActiveStep(WORKFLOW_STEPS.bundle);
        startPolling();
      },
    });
  }

  async function viewBundle() {
    if (!selectedJobId) return;
    setActionLoading(true);
    try {
      setBundleResult(await getBundle(selectedJobId));
      setActiveStep(WORKFLOW_STEPS.bundle);
    } catch (err) {
      setDetailsError(err.message);
    } finally {
      setActionLoading(false);
    }
  }

  async function importMongoAction() {
    if (!selectedJobId) return;
    setActionLoading(true);
    try {
      setMongoResult(await importMongo(selectedJobId));
      setSuccessMessage("Đã import MongoDB.");
      await loadSelectedJob(selectedJobId, { keepMessage: true, keepReview: true, keepResults: true, keepStep: true });
    } catch (err) {
      setDetailsError(err.message);
    } finally {
      setActionLoading(false);
    }
  }

  async function viewMongoResult() {
    if (!selectedJobId) return;
    setActionLoading(true);
    try {
      setMongoResult(await getMongoImportResult(selectedJobId));
      setActiveStep(WORKFLOW_STEPS.bundle);
    } catch (err) {
      setDetailsError(err.message);
    } finally {
      setActionLoading(false);
    }
  }

  const backendOk = healthInfo?.status === "ok";
  const selectedStatus = job?.status || status?.status;
  const isGeminiCooldown = selectedStatus === "waiting_gemini_cooldown" || /all gemini api keys are in cooldown|gemini api key đang tạm cooldown|tất cả gemini/i.test(`${status?.message || ""} ${job?.error || ""}`);
  const geminiCooldownSeconds = status?.cooldown_seconds || 300;
  const isBusy = actionLoading || BUSY_STATUSES.has(selectedStatus);
  const shortJobId = selectedJobId ? `${selectedJobId.slice(0, 8)}...${selectedJobId.slice(-4)}` : "-";
  const rawData = {
    job,
    status,
    topic_debug: {
      topics_partial_exists: Boolean(status?.topics_partial_exists || job?.topics_partial_exists || status?.has_topics || job?.has_topics),
      topic_count: status?.topic_count ?? job?.topic_count ?? (Array.isArray(topics) ? topics.length : 0),
      current_status: selectedStatus,
      can_review_topics: Boolean(status?.can_review_topics || job?.can_review_topics || (Array.isArray(topics) && topics.length > 0)),
    },
    topics,
    lessons,
    chunks,
    bundleResult,
    mongoResult,
    minio: job?.minio,
  };
  const approvedChunkCount = Array.isArray(chunks)
    ? chunks.filter((chunk) => chunk?.approved || chunk?.waiting_for_kaggle || chunk?.kaggle_finalized || chunk?.metadata_edu_saved).length
    : 0;
  const approvedTopicCount = Array.isArray(topics)
    ? topics.filter((topic) => topic?.approved || topic?.metadata_edu_saved || topic?.minio_uploaded).length
    : 0;
  const approvedLessonCount = Array.isArray(lessons)
    ? lessons.filter((lesson) => lesson?.approved || lesson?.metadata_edu_saved || lesson?.minio_uploaded).length
    : 0;
  const hasApprovedChunks = approvedChunkCount > 0;
  const hasApprovedTopics = approvedTopicCount > 0;
  const hasApprovedLessons = approvedLessonCount > 0;

  function goBack() {
    const order = Object.values(WORKFLOW_STEPS);
    const index = order.indexOf(activeStep);
    setDetailsError("");
    setNavWarning("");
    setActiveStep(order[Math.max(0, index - 1)]);
  }

  function goNext() {
    setDetailsError("");
    setNavWarning("");
    if (activeStep === WORKFLOW_STEPS.upload) {
      if (!selectedJobId) {
        setNavWarning("Bạn cần chọn hoặc tạo job trước khi tiếp tục.");
        return;
      }
      setActiveStep(WORKFLOW_STEPS.topics);
      return;
    }
    if (activeStep === WORKFLOW_STEPS.topics && !hasApprovedTopics) {
      setNavWarning("Bạn cần duyệt ít nhất một chủ đề trước khi sang bước bài học.");
      return;
    }
    if (activeStep === WORKFLOW_STEPS.lessons && !hasApprovedLessons) {
      setNavWarning("Bạn cần duyệt ít nhất một bài học trước khi sang bước chunk.");
      return;
    }
    if (activeStep === WORKFLOW_STEPS.chunks && !hasApprovedChunks) {
      setNavWarning("Bạn cần duyệt ít nhất một chunk trước khi chạy Kaggle.");
      return;
    }
    const order = Object.values(WORKFLOW_STEPS);
    const index = order.indexOf(activeStep);
    setActiveStep(order[Math.min(order.length - 1, index + 1)]);
  }

  function canEnterStep(step) {
    if (step === WORKFLOW_STEPS.upload || step === WORKFLOW_STEPS.topics) return true;
    if (step === WORKFLOW_STEPS.lessons) return hasApprovedTopics || Array.isArray(lessons);
    if (step === WORKFLOW_STEPS.chunks) return hasApprovedLessons || Array.isArray(chunks);
    if (step === WORKFLOW_STEPS.bundle) return hasApprovedChunks || Boolean(bundleResult || mongoResult);
    return false;
  }

  function changeStep(step) {
    setDetailsError("");
    setNavWarning("");
    if (!selectedJobId && step !== WORKFLOW_STEPS.upload) {
      setNavWarning("Bạn cần chọn hoặc tạo một phiên duyệt trước khi tiếp tục.");
      return;
    }
    if (!canEnterStep(step)) {
      const messageByStep = {
        lessons: "Bạn cần duyệt ít nhất một chủ đề trước khi sang bước bài học.",
        chunks: "Bạn cần duyệt ít nhất một bài học trước khi sang bước chunk.",
        bundle: "Bạn cần duyệt ít nhất một chunk trước khi chạy Kaggle.",
      };
      setNavWarning(messageByStep[step] || "Bạn cần duyệt bước hiện tại trước khi tiếp tục.");
      return;
    }
    setActiveStep(step);
  }

  function beginCreateSession() {
    stopPolling();
    setSelectedJobId("");
    setJob(null);
    setStatus(null);
    setDetailsError("");
    setNavWarning("");
    setSuccessMessage("");
    resetReviewData();
    setActiveStep(WORKFLOW_STEPS.upload);
  }

  return (
    <div className="appShell review-shell workspaceShell">
      <aside className="appSidebar">
        <div className="sidebarBrand">
          <span className="brandMark">AI</span>
          <div>
            <h1>AI Tra Cứu</h1>
            <p>Review-first Metadata</p>
          </div>
        </div>
        <button type="button" className="primaryButton newSessionButton" onClick={beginCreateSession}>+ Tạo phiên duyệt</button>
        {jobsError ? <ErrorState message={jobsError} onRetry={loadJobs} /> : null}
        <JobList jobs={jobs} selectedJobId={selectedJobId} loading={jobsLoading} onSelect={setSelectedJobId} onRefresh={loadJobs} />
        <div className="sidebarFooter">
          <button type="button" className={`healthBadge ${backendOk ? "ok" : "down"}`} onClick={checkHealth}>
            Backend: {backendOk ? "OK" : healthError ? "Không kết nối được" : "Đang kiểm tra..."}
          </button>
          <span className="smallText">Metadata-Edu pipeline</span>
        </div>
      </aside>

      <main className="appMain">
        <header className="workspaceTopbar">
          <div className="workspaceTitle">
            <h2>{selectedJobId && activeStep !== WORKFLOW_STEPS.upload ? (job?.book_name || "Phiên duyệt") : "Tạo phiên duyệt mới"}</h2>
            <p>
              {selectedJobId && activeStep !== WORKFLOW_STEPS.upload
                ? `Khối ${job?.class_name || "-"} · ${job?.subject_name || "-"} · ${job?.subject_type || "-"}`
                : "Tải sách PDF để bắt đầu quy trình duyệt metadata"}
            </p>
          </div>
          <div className="workspaceActions">
            {selectedStatus && activeStep !== WORKFLOW_STEPS.upload ? <JobStatusBadge status={selectedStatus} /> : null}
            {selectedJobId && activeStep !== WORKFLOW_STEPS.upload ? (
              <button type="button" onClick={() => window.open(getSourcePreviewUrl(selectedJobId), "_blank", "noopener,noreferrer")}>
                Xem sách gốc
              </button>
            ) : null}
            <button type="button" className="secondary-action" onClick={() => setDebugOpen((value) => !value)}>
              {debugOpen ? "Đóng debug" : "Mở debug"}
            </button>
          </div>
        </header>

        {healthError ? <div className="warningBox inlineNotice">{healthError}</div> : null}
        {successMessage ? <div className="successBanner compactBanner">{successMessage}</div> : null}
        {selectedJobId && isBusy ? <ProgressBanner status={status} fallback="Đang xử lý dữ liệu..." /> : null}
        {selectedJobId && isGeminiCooldown ? (
          <div className="cooldownNotice">
            <div>
              <strong>Tất cả Gemini API key đang tạm nghỉ.</strong>
              <span>
                Hệ thống sẽ thử lại sau khi cooldown kết thúc.
              </span>
              <span>
                Thời gian chờ dự kiến: {geminiCooldownSeconds} giây
                {status?.next_available_at ? ` · Khả dụng lại: ${status.next_available_at}` : ""}
              </span>
            </div>
            <button
              type="button"
              onClick={() => runAction(() => retryGeminiStage(selectedJobId), {
                success: "Đã bắt đầu thử lại bước Gemini.",
                reload: async () => startPolling(),
              })}
              disabled={actionLoading}
            >
              Thử lại ngay
            </button>
          </div>
        ) : null}

        {!selectedJobId || activeStep === WORKFLOW_STEPS.upload ? (
          <section className="workspaceUpload">
            <BookUploadForm onUploaded={afterUpload} />
          </section>
        ) : null}

        {selectedJobId && activeStep !== WORKFLOW_STEPS.upload ? (
          <>
            <section className="documentContextCard">
              <div className="book-summary-card">
                <div className="source-book-thumbnail pdfCoverPlaceholder" aria-label="Sách gốc">
                  <span>PDF</span>
                  <strong>Sách gốc</strong>
                  <small>{job?.book_name || "Tài liệu"}</small>
                </div>
                <div className="currentDocument">
                  <strong>{job?.book_name || "Tài liệu chưa đặt tên"}</strong>
                  <div className="bookMetaChips">
                    <span>Khối {job?.class_name || "-"}</span>
                    <span>{job?.subject_name || "-"}</span>
                    <span>{job?.subject_type || "-"}</span>
                    <span>{shortJobId}</span>
                    <em>{job?.minio?.subject_asset_uploaded ? "Đã tải MinIO" : BUSY_STATUSES.has(selectedStatus) ? "Đang xử lý" : "Chờ duyệt"}</em>
                  </div>
                </div>
              </div>
            </section>
            <ReviewStepper status={selectedStatus} activeStep={activeStep} onStepChange={changeStep} />
          </>
        ) : null}

        {selectedJobId && activeStep !== WORKFLOW_STEPS.upload ? <section className="contentArea workspaceContent">
          {!selectedJobId && !jobsLoading ? (
            <EmptyState title="Chưa chọn job" message="Upload hoặc chọn một sách/job ở danh sách bên trái để bắt đầu review." />
          ) : null}

          {selectedJobId && detailsLoading ? <LoadingState message="Đang tải chi tiết job..." /> : null}
          {selectedJobId && detailsError ? <ErrorState message={detailsError} onRetry={() => loadSelectedJob(selectedJobId)} /> : null}
          {selectedJobId && navWarning ? <div className="warningBox inlineNotice">{navWarning}</div> : null}

          {selectedJobId && job && !detailsLoading ? (
            <>
              {activeStep === WORKFLOW_STEPS.topics ? (
                <TopicReviewView
                  jobId={selectedJobId}
                  topics={topics}
                  approved={topicsApproved}
                  loading={actionLoading}
                  status={status}
                  jobStatus={selectedStatus}
                  error={topicsError}
                  onChange={(index, field, value) => updateItem(setTopics, index, field, value)}
                  onLoad={loadTopics}
                  onExtract={() => runAction(() => extractTopics(selectedJobId), {
                    loadingMessage: "Đang trích xuất chủ đề...",
                    success: "Đang trích xuất chủ đề. Theo dõi tiến độ trong debug khi cần.",
                    reload: async () => startPolling(),
                    onError: setTopicsError,
                  })}
                  onSave={() => runAction(() => saveTopics(selectedJobId, topics || []), { success: "Đã lưu chỉnh sửa.", reload: loadTopics, onError: setTopicsError })}
                  onApproveAll={() => runAction(() => approveTopics(selectedJobId, topics || []), {
                    success: "Đã lưu toàn bộ chủ đề.",
                    reload: loadTopics,
                    onError: setTopicsError,
                  })}
                  onApproveTopic={(topic) => runAction(() => approveTopic(selectedJobId, topic.topic_num), {
                    success: `Đã lưu Topic ${String(topic.topic_num).padStart(2, "0")} vào MongoDB và MinIO.`,
                    reload: async () => {
                      await loadTopics();
                    },
                    onError: setTopicsError,
                  })}
                  onExtractLessonsForTopic={(topic) => {
                    setSelectedTopicNum(String(topic.topic_num || ""));
                    return runAction(() => extractLessonsForTopic(selectedJobId, topic.topic_num), {
                      loadingMessage: `Đang trích xuất bài học cho Topic ${String(topic.topic_num).padStart(2, "0")}...`,
                      success: `Đang trích xuất bài học cho Topic ${String(topic.topic_num).padStart(2, "0")}.`,
                      reload: async () => {
                        setActiveStep(WORKFLOW_STEPS.lessons);
                        startPolling();
                      },
                      onError: setTopicsError,
                    });
                  }}
                  onBack={goBack}
                  onNext={goNext}
                />
              ) : null}

              {activeStep === WORKFLOW_STEPS.lessons ? (
                <LessonReviewView
                  jobId={selectedJobId}
                  lessons={lessons}
                  groupedByTopic={groupedLessons}
                  selectedTopicNum={selectedTopicNum}
                  approved={lessonsApproved}
                  loading={actionLoading}
                  status={status}
                  jobStatus={selectedStatus}
                  error={lessonsError}
                  onChange={(index, field, value) => updateItem(setLessons, index, field, value)}
                  onLoad={loadLessons}
                  onExtract={() => runAction(() => extractLessons(selectedJobId), {
                    loadingMessage: "Đang trích xuất bài học...",
                    success: "Đang trích xuất bài học.",
                    reload: async () => startPolling(),
                    onError: setLessonsError,
                  })}
                  onSave={() => runAction(() => saveLessons(selectedJobId, lessons || []), { success: "Đã lưu chỉnh sửa bài học.", reload: loadLessons, onError: setLessonsError })}
                  onApprove={() => runAction(() => approveLessons(selectedJobId, lessons || []), {
                    success: "Đã lưu metadata bài học. Tiếp tục trích xuất chunk.",
                    reload: async () => {
                      await loadLessons();
                      setActiveStep(WORKFLOW_STEPS.chunks);
                    },
                    onError: setLessonsError,
                  })}
                  onApproveLesson={(lesson) => runAction(() => approveLesson(selectedJobId, lesson.lesson_num), {
                    success: `Đã lưu Lesson ${String(lesson.lesson_num).padStart(2, "0")} vào MongoDB/MinIO.`,
                    reload: loadLessons,
                    onError: setLessonsError,
                  })}
                  onExtractChunksForLesson={(lesson) => {
                    setSelectedLessonNum(String(lesson.lesson_num || ""));
                    return runAction(() => extractChunksForLesson(selectedJobId, lesson.lesson_num), {
                      loadingMessage: `Đang trích xuất chunk cho Lesson ${String(lesson.lesson_num).padStart(2, "0")}...`,
                      success: `Đang trích xuất chunk cho Lesson ${String(lesson.lesson_num).padStart(2, "0")}.`,
                      reload: async () => {
                        setActiveStep(WORKFLOW_STEPS.chunks);
                        startPolling();
                      },
                      onError: setLessonsError,
                    });
                  }}
                  onBack={goBack}
                  onNext={goNext}
                />
              ) : null}

              {activeStep === WORKFLOW_STEPS.chunks ? (
                <ChunkReviewView
                  jobId={selectedJobId}
                  chunks={chunks}
                  groupedByLesson={groupedChunks}
                  approved={chunksApproved}
                  loading={actionLoading}
                  status={status}
                  jobStatus={selectedStatus}
                  error={chunksError}
                  onChange={(index, field, value) => updateItem(setChunks, index, field, value)}
                  onLoad={loadChunks}
                  onExtract={() => runAction(() => extractChunks(selectedJobId), {
                    loadingMessage: "Đang trích xuất chunk bằng Gemini...",
                    success: "Đang trích xuất chunk bằng Gemini...",
                    reload: async () => startPolling(),
                    onError: setChunksError,
                  })}
                  onSave={() => runAction(() => saveChunks(selectedJobId, chunks || []), { success: "Đã lưu chỉnh sửa chunk.", reload: loadChunks, onError: setChunksError })}
                  onApprove={(chunkSubset) => runAction(() => approveChunks(selectedJobId, chunkSubset || chunks || []), {
                    success: "Đã duyệt chunk. Chunk sẽ được lưu vào MongoDB/MinIO sau khi Kaggle xử lý xong.",
                    reload: async () => {
                      await loadChunks();
                      setActiveStep(WORKFLOW_STEPS.bundle);
                    },
                    onError: setChunksError,
                    poll: false,
                  })}
                  onApproveChunk={(chunk) => runAction(() => approveChunk(selectedJobId, chunk.chunk_id || chunk.id), {
                    loadingMessage: "Đang đánh dấu duyệt chunk...",
                    success: "Chunk đã duyệt. Chunk sẽ được lưu vào MongoDB/MinIO sau khi Kaggle xử lý xong.",
                    reload: loadChunks,
                    onError: setChunksError,
                    poll: false,
                  })}
                  onApproveChunkIds={(chunkIds) => runAction(() => approveChunkIds(selectedJobId, chunkIds), {
                    loadingMessage: "Đang đánh dấu duyệt chunk...",
                    success: "Đã duyệt các chunk trong bài. Các chunk này đang chờ Kaggle xử lý.",
                    reload: loadChunks,
                    onError: setChunksError,
                    poll: false,
                  })}
                  onAdd={(payload) => runAction(() => addChunk(selectedJobId, payload), { success: "Đã thêm chunk.", reload: loadChunks, onError: setChunksError })}
                  onDelete={(chunkId) => runAction(() => deleteChunk(selectedJobId, chunkId), { success: "Đã xóa chunk.", reload: loadChunks, onError: setChunksError })}
                  onRecut={(chunk) => runAction(() => recutChunk(selectedJobId, {
                    chunk_id: chunk.chunk_id || chunk.id,
                    lesson_stem: chunk.lesson_stem,
                    chunk_num: chunk.chunk_num,
                    start: Number(chunk.start),
                    end: Number(chunk.end),
                    heading: chunk.heading || "",
                    title: chunk.title || chunk.chunk_name || "",
                    content_head: Boolean(chunk.content_head),
                  }), { success: "Đã cắt lại chunk.", reload: loadChunks, onError: setChunksError })}
                  onBack={goBack}
                  onNext={goNext}
                />
              ) : null}

              {activeStep === WORKFLOW_STEPS.bundle ? (
                <BundleView
                  loading={actionLoading}
                  bundleResult={bundleResult}
                  mongoResult={mongoResult}
                  onPrepare={() => prepareBundleAction()}
                  onPrepareFast={() => prepareBundleAction({ skip_kaggle: true, skip_keywords: true })}
                  onViewBundle={viewBundle}
                  onDownloadBundle={() => downloadBundle(selectedJobId)}
                  onImportMongo={importMongoAction}
                  onViewMongo={viewMongoResult}
                  onFinalizeChunks={() => runAction(() => finalizeChunksAfterKaggle(selectedJobId, { force_without_kaggle: true }), {
                    success: "Đã lưu chunk cuối vào MongoDB/MinIO.",
                    reload: viewBundle,
                  })}
                  onBack={goBack}
                />
              ) : null}
            </>
          ) : null}
        </section> : null}
      </main>

      <aside className={`debugDrawer debug-drawer ${debugOpen ? "open" : ""}`}>
        <div className="debugDrawerHeader">
          <div>
            <strong>Debug phiên duyệt</strong>
            <span className="mono">{selectedJobId || "Chưa chọn job"}</span>
          </div>
          <button type="button" onClick={() => setDebugOpen(false)}>Đóng</button>
        </div>
        <div className="debugArea">
          <section className="panel inspectorCard">
            <h3>Liên kết nguồn</h3>
            <dl className="statusGrid">
              <dt>Backend</dt><dd className="mono breakText">{API_BASE_URL}</dd>
              <dt>Source</dt><dd className="mono breakText">{selectedJobId ? getSourcePreviewUrl(selectedJobId) : "-"}</dd>
              <dt>Object key</dt><dd className="mono breakText">{job?.minio?.subject_object_key || "-"}</dd>
            </dl>
          </section>
          <section className="panel inspectorCard">
            <h3>Debug chủ đề</h3>
            <dl className="statusGrid">
              <dt>topics_partial</dt><dd>{rawData.topic_debug.topics_partial_exists ? "Có" : "Không"}</dd>
              <dt>Số topic</dt><dd>{rawData.topic_debug.topic_count}</dd>
              <dt>Status</dt><dd>{rawData.topic_debug.current_status || "-"}</dd>
              <dt>Cho phép duyệt</dt><dd>{rawData.topic_debug.can_review_topics ? "Có" : "Không"}</dd>
            </dl>
          </section>
          <StatusPanel job={job} status={status} />
          <LogPanel log={logs} onRefresh={loadLogs} loading={actionLoading} />
          <RawJsonPanel title="JSON gốc" data={rawData} open={rawOpen} onToggle={() => setRawOpen((value) => !value)} />
        </div>
      </aside>
    </div>
  );
}

function ProgressBanner({ status, fallback }) {
  const rawPercent = Number(status?.percent);
  const hasPercent = Number.isFinite(rawPercent) && rawPercent > 0;
  const percent = Math.max(0, Math.min(rawPercent || 0, 100));
  const message = friendlyProgress(status, fallback);

  return (
    <section className="progressBanner">
      <div className="progressSpinner" aria-hidden="true" />
      <div>
        <strong>Đang xử lý</strong>
        <span>{message}</span>
      </div>
      <div className={`progressTrack ${hasPercent ? "" : "indeterminate"}`}>
        <div className="progressFill" style={{ width: hasPercent ? `${percent}%` : "42%" }} />
      </div>
      <span className="progressPercent">{hasPercent ? `${percent}%` : "Đang chạy"}</span>
    </section>
  );
}
