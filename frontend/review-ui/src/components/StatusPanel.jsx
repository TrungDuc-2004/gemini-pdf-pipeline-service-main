export default function StatusPanel({ status, job }) {
  const percent = Number.isFinite(Number(status?.percent)) ? Number(status.percent) : 0;
  return (
    <section className="panel inspectorCard">
      <div className="panelHeader">
        <div>
          <h2>Trạng thái</h2>
          <p className="muted">Snapshot mới nhất của job.</p>
        </div>
      </div>
      <dl className="statusGrid">
        <dt>Job ID</dt>
        <dd className="mono">{job?.job_id || status?.job_id || "-"}</dd>
        <dt>Status</dt>
        <dd>{status?.status || job?.status || "-"}</dd>
        <dt>Stage</dt>
        <dd>{status?.stage || job?.stage || "-"}</dd>
        <dt>Tiến độ</dt>
        <dd>{percent}%</dd>
        <dt>Thông báo</dt>
        <dd>{status?.message || "-"}</dd>
        <dt>Cooldown</dt>
        <dd>
          {status?.status === "waiting_gemini_cooldown"
            ? `Dự kiến ${status?.cooldown_seconds || 300} giây${status?.next_available_at ? ` · ${status.next_available_at}` : ""}`
            : status?.next_available_at || "-"}
        </dd>
        <dt>MinIO</dt>
        <dd>{job?.minio?.subject_asset_uploaded ? `Đã upload ${job.minio.bucket}` : "-"}</dd>
        <dt>PDF nguồn</dt>
        <dd className="mono breakText">{job?.source_pdf_path || job?.paths?.source_pdf_path || "-"}</dd>
      </dl>
      <div className="progressTrack">
        <div className="progressFill" style={{ width: `${Math.min(percent, 100)}%` }} />
      </div>
    </section>
  );
}
