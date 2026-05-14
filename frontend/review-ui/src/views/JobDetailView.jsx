import StatusPanel from "../components/StatusPanel.jsx";

export default function JobDetailView({
  jobId,
  job,
  status,
  onRefreshStatus,
  onExtractTopics,
  onLoadTopics,
  onExtractLessons,
  onLoadLessons,
  onExtractChunks,
  onLoadChunks,
  onLoadLogs,
  loading,
}) {
  return (
    <div className="stack">
      <StatusPanel job={job} status={status} />
      <section className="panel">
        <div className="panelHeader">
          <h2>Thao tác job</h2>
        </div>
        <div className="buttonRow">
          <button type="button" onClick={onRefreshStatus} disabled={!jobId || loading}>
            Làm mới trạng thái
          </button>
          <button type="button" onClick={onExtractTopics} disabled={!jobId || loading}>
            Trích xuất chủ đề
          </button>
          <button type="button" onClick={onLoadTopics} disabled={!jobId || loading}>
            Tải danh sách chủ đề
          </button>
          <button type="button" onClick={onExtractLessons} disabled={!jobId || loading}>
            Trích xuất bài học
          </button>
          <button type="button" onClick={onLoadLessons} disabled={!jobId || loading}>
            Tải danh sách bài học
          </button>
          <button type="button" onClick={onExtractChunks} disabled={!jobId || loading}>
            Trích xuất chunk
          </button>
          <button type="button" onClick={onLoadChunks} disabled={!jobId || loading}>
            Tải danh sách chunk
          </button>
          <button type="button" onClick={onLoadLogs} disabled={!jobId || loading}>
            Xem log
          </button>
        </div>
      </section>
    </div>
  );
}
