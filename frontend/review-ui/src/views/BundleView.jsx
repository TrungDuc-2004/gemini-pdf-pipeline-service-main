export default function BundleView({
  loading,
  bundleResult,
  mongoResult,
  onPrepare,
  onPrepareFast,
  onViewBundle,
  onDownloadBundle,
  onImportMongo,
  onViewMongo,
  onFinalizeChunks,
  onBack,
}) {
  const bundleCounts = bundleResult?.counts || bundleResult?.data?.counts || {};
  const mongoCounts = mongoResult?.counts || mongoResult?.data?.counts || {};
  const assetCounts = bundleResult?.assets || bundleResult?.asset_counts || {};
  const finalizedChunks = bundleResult?.chunk_finalize_summary?.counts?.chunk_count ?? bundleResult?.data?.chunk_finalize_summary?.counts?.chunk_count;
  const keywordCount = bundleResult?.keyword_summary?.metadata_edu?.counts?.keyword_count ?? bundleResult?.data?.keyword_summary?.metadata_edu?.counts?.keyword_count;

  return (
    <section className="panel reviewCard finishWorkspace">
      <div className="topicReviewHeader">
        <div>
          <span className="stepLabel">Bước 5</span>
          <h2>Bước 5: Hoàn tất dữ liệu</h2>
          <p className="muted">Chạy Kaggle, lưu chunk cuối, trích xuất keyword và hoàn tất dữ liệu.</p>
        </div>
        <div className="topicHeaderActions">
          <button type="button" className="primaryButton primary-action" onClick={onPrepare} disabled={loading}>Chạy xử lý hoàn tất</button>
          <button type="button" onClick={onPrepareFast} disabled={loading}>Tạo bundle nhanh</button>
          <button type="button" onClick={onDownloadBundle} disabled={loading}>Tải ZIP</button>
          <button type="button" onClick={onViewMongo} disabled={loading}>Xem kết quả MongoDB</button>
        </div>
      </div>

      <div className="bundleGrid">
        <div className="bundleBlock">
          <h3>Kaggle OCR/cutline</h3>
          <p className="muted">Xử lý cutline và chuẩn bị dữ liệu chunk cuối.</p>
          <button type="button" onClick={onPrepare} disabled={loading}>Chạy Kaggle và lưu chunk cuối</button>
        </div>

        <div className="bundleBlock">
          <h3>Lưu chunk cuối vào MongoDB/MinIO</h3>
          <p className="muted">Finalize chunk sau khi dữ liệu Kaggle sẵn sàng.</p>
          <button type="button" onClick={onFinalizeChunks} disabled={loading}>Lưu chunk cuối</button>
        </div>

        <div className="bundleBlock">
          <h3>Trích xuất keyword</h3>
          <p className="muted">Keyword được chạy trong bước hoàn tất khi cấu hình bật.</p>
          <button type="button" onClick={onViewBundle} disabled={loading}>Xem thống kê keyword</button>
        </div>

        <div className="bundleBlock">
          <h3>Tổng kết dữ liệu</h3>
          <p className="muted">Kiểm tra kết quả lưu metadata cuối cùng.</p>
          <div className="buttonRow">
            <button type="button" className="primaryButton" onClick={onImportMongo} disabled={loading}>Lưu metadata</button>
            <button type="button" onClick={onViewMongo} disabled={loading}>Xem kết quả lưu MongoDB</button>
          </div>
        </div>
      </div>

      <div className="bundleGrid resultGrid">
        <div className="bundleBlock">
          <h3>Thống kê dữ liệu</h3>
          <dl className="statusGrid">
            <dt>Chủ đề đã lưu</dt>
            <dd>{bundleCounts.topics ?? bundleCounts.topic_count ?? "-"}</dd>
            <dt>Bài học đã lưu</dt>
            <dd>{bundleCounts.lessons ?? bundleCounts.lesson_count ?? "-"}</dd>
            <dt>Chunk chờ Kaggle</dt>
            <dd>{bundleCounts.chunks ?? bundleCounts.chunk_count ?? "-"}</dd>
            <dt>Chunk đã lưu sau Kaggle</dt>
            <dd>{finalizedChunks ?? "-"}</dd>
            <dt>Keyword đã lưu</dt>
            <dd>{keywordCount ?? "-"}</dd>
          </dl>
          {Object.keys(bundleCounts).length ? <CountGrid counts={bundleCounts} /> : <p className="muted">Chưa có thống kê bundle.</p>}
          {Object.keys(assetCounts).length ? (
            <>
              <h4>Tài sản PDF / keyword</h4>
              <CountGrid counts={assetCounts} keys={["topic_pdfs", "lesson_pdfs", "chunk_pdfs", "keyword_files"]} />
            </>
          ) : null}
        </div>

        <div className="bundleBlock">
          <h3>Kết quả lưu metadata</h3>
          <dl className="statusGrid">
            <dt>Trạng thái</dt>
            <dd>{mongoResult?.status || "-"}</dd>
            <dt>Database</dt>
            <dd>{mongoResult?.db_name || "-"}</dd>
            <dt>Hoàn tất</dt>
            <dd>{mongoResult?.completed_at || "-"}</dd>
          </dl>
          {Object.keys(mongoCounts).length ? <CountGrid counts={mongoCounts} /> : <p className="muted">Chưa có kết quả import.</p>}
        </div>
      </div>

      <details className="advancedBox subtleAdvanced">
        <summary>Tuỳ chọn nâng cao</summary>
        <div className="actionBar advancedActionBar">
          <button type="button" onClick={onViewBundle} disabled={loading}>Làm mới thống kê bundle</button>
          <button type="button" onClick={onFinalizeChunks} disabled={loading}>Lưu chunk cuối thủ công</button>
        </div>
      </details>
      <div className="wizardNav">
        <button type="button" onClick={onBack}>Quay lại</button>
      </div>
    </section>
  );
}

function CountGrid({ counts, keys }) {
  const countKeys = keys || ["class_count", "subject_count", "topic_count", "lesson_count", "chunk_count", "keyword_count", "asset_count"];
  return (
    <div className="countGrid">
      {countKeys.map((key) => (
        <div className="countCard" key={key}>
          <span>{key}</span>
          <strong>{counts[key] ?? 0}</strong>
        </div>
      ))}
    </div>
  );
}
