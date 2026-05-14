import { useState } from "react";
import { createJob } from "../api/client.js";

export default function JobCreateView({ onCreated, lastJobId, onLoadLast }) {
  const [form, setForm] = useState({
    book_name: "",
    class_name: "",
    subject_name: "",
    subject_type: "",
    enable_keywords: true,
    enable_kaggle: false,
  });
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  function updateField(name, value) {
    setForm((current) => ({ ...current, [name]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    if (!file) {
      setError("Vui lòng chọn file PDF.");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    body.append("book_name", form.book_name);
    body.append("class_name", form.class_name);
    body.append("subject_name", form.subject_name);
    body.append("subject_type", form.subject_type);
    body.append("pipeline_mode", "review_first");
    body.append("enable_keywords", String(form.enable_keywords));
    body.append("enable_kaggle", String(form.enable_kaggle));

    setLoading(true);
    try {
      const created = await createJob(body);
      onCreated(created.job_id, created);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Tạo job mới</h2>
      </div>
      {lastJobId ? (
        <div className="lastJobBox">
          <span>Job gần nhất: <span className="mono">{lastJobId}</span></span>
          <button type="button" onClick={onLoadLast}>Tải job gần nhất</button>
        </div>
      ) : null}
      <form className="formGrid" onSubmit={handleSubmit}>
        <label>
          <span>Tải sách PDF</span>
          <input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </label>
        <label>
          <span>Tên sách</span>
          <input required value={form.book_name} onChange={(e) => updateField("book_name", e.target.value)} />
        </label>
        <label>
          <span>Lớp</span>
          <input required value={form.class_name} onChange={(e) => updateField("class_name", e.target.value)} />
        </label>
        <label>
          <span>Môn học</span>
          <input required value={form.subject_name} onChange={(e) => updateField("subject_name", e.target.value)} />
        </label>
        <label>
          <span>Bộ sách / Loại môn học</span>
          <input value={form.subject_type} onChange={(e) => updateField("subject_type", e.target.value)} />
        </label>
        <label className="checkboxRow">
          <input
            type="checkbox"
            checked={form.enable_keywords}
            onChange={(e) => updateField("enable_keywords", e.target.checked)}
          />
          <span>Bật trích xuất keyword</span>
        </label>
        <label className="checkboxRow">
          <input
            type="checkbox"
            checked={form.enable_kaggle}
            onChange={(e) => updateField("enable_kaggle", e.target.checked)}
          />
          <span>Bật Kaggle OCR/cutline</span>
        </label>
        {error ? <div className="errorBox">{error}</div> : null}
        <button className="primaryButton" type="submit" disabled={loading}>
          {loading ? "Đang tạo..." : "Tạo job"}
        </button>
      </form>
    </section>
  );
}
