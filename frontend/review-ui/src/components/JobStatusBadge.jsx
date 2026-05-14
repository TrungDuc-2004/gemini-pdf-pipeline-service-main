const LABELS = {
  uploaded: "Đã tải sách",
  waiting_gemini_cooldown: "Chờ Gemini cooldown",
  extracting_topics: "Đang trích xuất chủ đề",
  reviewing_topics: "Chờ duyệt chủ đề",
  extracting_lessons: "Đang trích xuất bài học",
  reviewing_lessons: "Chờ duyệt bài học",
  extracting_chunks: "Đang trích xuất chunk",
  reviewing_chunks: "Chờ duyệt chunk",
  preparing_bundle: "Đang hoàn tất",
  running_kaggle: "Đang xử lý Kaggle OCR/cutline",
  extracting_keywords: "Đang trích xuất keyword",
  bundle_ready: "Sẵn sàng lưu",
  importing_mongodb: "Đang lưu metadata",
  mongodb_imported: "Đã lưu dữ liệu",
  error: "Lỗi",
};

export default function JobStatusBadge({ status }) {
  const value = status || "unknown";
  return <span className={`statusBadge status-${value}`}>{LABELS[value] || value}</span>;
}
