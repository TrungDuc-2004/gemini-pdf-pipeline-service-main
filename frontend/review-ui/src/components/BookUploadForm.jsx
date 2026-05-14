import { useState } from "react";
import { createJob } from "../api/reviewApi.js";

export default function BookUploadForm({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({
    book_name: "",
    class_name: "11",
    subject_name: "Tin học",
    subject_type: "Kết nối tri thức",
    enable_keywords: true,
    enable_kaggle: false,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [createdJobId, setCreatedJobId] = useState("");

  function update(name, value) {
    setForm((current) => ({ ...current, [name]: value }));
  }

  function titleFromFilename(filename = "") {
    const stem = filename.replace(/\.[^/.]+$/, "");
    return stem
      .replace(/[-_]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\p{L}+/gu, (word) => word.charAt(0).toLocaleUpperCase("vi-VN") + word.slice(1));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    setCreatedJobId("");
    if (!file) {
      setError("Vui lòng chọn file PDF.");
      return;
    }

    const documentName = form.book_name.trim() || titleFromFilename(file.name) || "Tài liệu PDF";
    const body = new FormData();
    body.append("file", file);
    body.append("book_name", documentName);
    body.append("class_name", form.class_name);
    body.append("subject_name", form.subject_name);
    body.append("subject_type", form.subject_type);
    body.append("pipeline_mode", "review_first");
    body.append("enable_keywords", String(form.enable_keywords));
    body.append("enable_kaggle", String(form.enable_kaggle));

    setLoading(true);
    try {
      const created = await createJob(body);
      setCreatedJobId(created?.job_id || "");
      onUploaded?.(created);
    } catch (err) {
      setError(err.message || "Upload thất bại.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel uploadCard">
      <div className="panelHeader">
        <div>
          <h2>Tạo phiên duyệt mới</h2>
          <p className="muted">Chọn sách PDF để bắt đầu quy trình trích xuất và duyệt metadata.</p>
        </div>
      </div>
      <form className="formGrid" onSubmit={submit}>
        <label className="fileDrop">
          <span className="fileDropIcon">PDF</span>
          <strong>{file ? file.name : "Kéo thả hoặc chọn file PDF"}</strong>
          <small>Hỗ trợ sách giáo khoa định dạng PDF</small>
          <input className="nativeFileInput" type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.target.files?.[0] || null)} />
        </label>
        <div className="defaultHintChips" aria-label="Cấu hình mặc định">
          <span>Khối 11</span>
          <span>Tin học</span>
          <span>Kết nối tri thức</span>
        </div>
        <label>
          <span>Tên tài liệu (tuỳ chọn)</span>
          <input
            value={form.book_name}
            placeholder="Tự điền theo tên file nếu bỏ trống"
            onChange={(event) => update("book_name", event.target.value)}
          />
        </label>
        <div className="twoColumn">
          <label>
            <span>Section / Khối lớp</span>
            <input required value={form.class_name} onChange={(event) => update("class_name", event.target.value)} />
          </label>
          <label>
            <span>Môn học</span>
            <input required value={form.subject_name} onChange={(event) => update("subject_name", event.target.value)} />
          </label>
        </div>
        <label>
          <span>Bộ sách</span>
          <input value={form.subject_type} onChange={(event) => update("subject_type", event.target.value)} />
        </label>
        <div className="toggleRow">
          <label className={`checkboxRow toggleChip ${form.enable_keywords ? "checked" : ""}`}>
            <input type="checkbox" checked={form.enable_keywords} onChange={(event) => update("enable_keywords", event.target.checked)} />
            <span>Trích xuất keyword</span>
          </label>
          <label className={`checkboxRow toggleChip ${form.enable_kaggle ? "checked" : ""}`}>
            <input type="checkbox" checked={form.enable_kaggle} onChange={(event) => update("enable_kaggle", event.target.checked)} />
            <span>Kaggle OCR/cutline</span>
          </label>
        </div>
        {error ? <div className="inlineError">{error}</div> : null}
        {createdJobId ? <div className="successBox">Sách đã được tải lên MinIO bucket ai-tra-cuu và sẵn sàng trích xuất chủ đề. Job: <span className="mono">{createdJobId}</span></div> : null}
        <button className="primaryButton primary-action" type="submit" disabled={loading}>
          {loading ? "Đang tạo phiên duyệt..." : "Tạo phiên duyệt"}
        </button>
      </form>
    </section>
  );
}
