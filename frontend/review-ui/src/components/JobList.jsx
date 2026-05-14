import { useMemo, useState } from "react";
import EmptyState from "./EmptyState.jsx";
import JobStatusBadge from "./JobStatusBadge.jsx";
import LoadingState from "./LoadingState.jsx";

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const diffMs = Date.now() - date.getTime();
  const minute = 60 * 1000;
  const hour = 60 * minute;
  if (diffMs >= 0 && diffMs < minute) return "vừa xong";
  if (diffMs >= minute && diffMs < hour) return `${Math.floor(diffMs / minute)} phút trước`;
  if (diffMs >= hour && diffMs < 24 * hour) return `${Math.floor(diffMs / hour)} giờ trước`;
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export default function JobList({ jobs, selectedJobId, loading, onSelect, onRefresh }) {
  const [query, setQuery] = useState("");
  const safeJobs = Array.isArray(jobs) ? jobs : [];
  const filteredJobs = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return safeJobs;
    return safeJobs.filter((job) =>
      [job.book_name, job.class_name, job.subject_name, job.job_id, job.status]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle))
    );
  }, [query, safeJobs]);

  function shortId(jobId = "") {
    return jobId ? `${jobId.slice(0, 8)}...${jobId.slice(-4)}` : "-";
  }

  return (
    <section className="panel jobListPanel">
      <div className="panelHeader">
        <div>
          <h2>Phiên duyệt gần đây</h2>
          <p className="muted">Tiếp tục các phiên đã tạo</p>
        </div>
        <button type="button" onClick={onRefresh} disabled={loading}>
          Làm mới
        </button>
      </div>
      <input
        className="searchInput"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Tìm theo tên sách, môn học hoặc mã phiên..."
      />
      {loading ? <LoadingState message="Đang tải phiên duyệt..." /> : null}
      {!loading && safeJobs.length === 0 ? (
        <div className="jobEmptyState">
          <span aria-hidden="true">PDF</span>
          <strong>Chưa có phiên duyệt nào</strong>
          <p>Bạn hãy tải sách PDF đầu tiên để bắt đầu.</p>
        </div>
      ) : null}
      {!loading && safeJobs.length > 0 && filteredJobs.length === 0 ? (
        <EmptyState message="Không tìm thấy job phù hợp với bộ lọc." />
      ) : null}
      {!loading && filteredJobs.length > 0 ? (
        <div className="jobList">
          {filteredJobs.map((job) => (
            <button
              type="button"
              key={job.job_id}
              className={`jobListItem ${selectedJobId === job.job_id ? "active" : ""}`}
              onClick={() => onSelect(job.job_id)}
            >
              <span className="jobAccent" aria-hidden="true" />
              <span className="jobTitle">{job.book_name || "Chưa đặt tên sách"}</span>
              <span className="jobSubline">Khối {job.class_name || "-"} · {job.subject_name || "-"} · {job.subject_type || "-"}</span>
              <span className="jobMeta">
                <JobStatusBadge status={job.status} />
                <span className="jobIdPill">{shortId(job.job_id)}</span>
                <span>{formatDateTime(job.updated_at || job.created_at)}</span>
              </span>
              <span className="continueHint">Tiếp tục duyệt</span>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}
