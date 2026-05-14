import { useEffect, useRef, useState } from "react";

/**
 * PdfPreviewCard — shows an iframe for a PDF preview URL.
 *
 * Strategy:
 *  1. Immediately show a loading state.
 *  2. Run a HEAD check against the URL.
 *  3a. If HEAD → 200, render the iframe (PDF loads normally).
 *  3b. If HEAD → non-200 or network error, show the friendly missing state.
 *
 * This prevents the browser from ever rendering a raw "Not Found" page
 * inside the iframe.
 */
function PdfPreviewCard({
  kind,
  label,
  url,
  pageHint,
  missingMessage,
  missingDetail,
  onLoadKey,
}) {
  const [headState, setHeadState] = useState(url ? "checking" : "missing");
  const [reloadVersion, setReloadVersion] = useState(0);
  const cancelRef = useRef(false);

  useEffect(() => {
    cancelRef.current = false;
    setHeadState(url ? "checking" : "missing");

    if (!url) return;

    async function checkPreview() {
      try {
        const response = await fetch(
          `${url}${url.includes("?") ? "&" : "?"}_check=${Date.now()}`,
          { method: "HEAD" },
        );
        if (!cancelRef.current) {
          setHeadState(response.ok ? "ok" : "missing");
        }
      } catch {
        if (!cancelRef.current) setHeadState("missing");
      }
    }

    checkPreview();

    return () => {
      cancelRef.current = true;
    };
  }, [url, onLoadKey, reloadVersion]);

  const iframeSrc = url && headState === "ok"
    ? `${url}${url.includes("?") ? "&" : "?"}_v=${reloadVersion}`
    : null;

  return (
    <section className={`pdf-preview-card ${kind}`}>
      <div className="pdfPreviewHeader">
        <div>
          <h3>{label}</h3>
          {pageHint ? <p>{pageHint}</p> : null}
        </div>
        {url && headState === "ok" ? (
          <button
            type="button"
            className="linkButton reloadPreviewBtn"
            title="Thử tải lại preview"
            onClick={() => setReloadVersion((v) => v + 1)}
          >
            ↺ Tải lại
          </button>
        ) : null}
      </div>

      {headState === "checking" ? (
        <div className="pdfFrameWrap pdfChecking">
          <div className="previewLoadingOverlay">Đang kiểm tra preview...</div>
        </div>
      ) : headState === "ok" && iframeSrc ? (
        <div className="pdfFrameWrap">
          <iframe
            className="pdf-frame"
            src={iframeSrc}
            title={label}
          />
        </div>
      ) : (
        <div className="pdfMissingState">
          <span className="pdfMissingIcon" aria-hidden="true">PDF</span>
          <strong>
            {missingMessage ||
              (kind === "source" ? "Chưa hiển thị được sách gốc" : "Chưa có PDF chủ đề")}
          </strong>
          <span className="pdfMissingDetail">
            {missingDetail ||
              (kind === "source"
                ? "File PDF đã được lưu, nhưng preview chưa sẵn sàng. Bạn có thể thử tải lại hoặc mở Debug."
                : "Hãy trích xuất chủ đề trước. Nếu đã trích xuất, kiểm tra Debug để xem đường dẫn preview.")}
          </span>
          {url ? (
            <button
              type="button"
              className="reloadPreviewBtn"
              onClick={() => setReloadVersion((v) => v + 1)}
            >
              Tải lại preview
            </button>
          ) : null}
        </div>
      )}
    </section>
  );
}

export default function SplitPdfReview({
  variant = "default",
  title,
  description,
  sourcePreviewUrl,
  extractedPreviewUrl,
  sourceLabel = "Sách giáo khoa gốc",
  extractedLabel = "Kết quả trích xuất",
  sourcePageHint,
  extractedPageHint,
  extractedStatusBadge,
  missingExtractedMessage,
  missingExtractedDetail,
  extractedDebugPaths,
  extractedDebugCandidates,
  children,
}) {
  const [activePreview, setActivePreview] = useState("compare");
  const isTopicVariant = variant === "topic";
  const sourceCard = (
    <PdfPreviewCard
      kind="source"
      label={sourceLabel}
      url={sourcePreviewUrl}
      pageHint={sourcePageHint}
      missingMessage="Không hiển thị được sách gốc."
      missingDetail="Hãy thử tải lại preview."
      onLoadKey={sourcePreviewUrl}
    />
  );
  const extractedCard = (
    <PdfPreviewCard
      kind="extracted"
      label={extractedLabel}
      url={extractedPreviewUrl}
      pageHint={extractedPageHint}
      missingMessage={missingExtractedMessage || "Không tìm thấy PDF chủ đề."}
      missingDetail={missingExtractedDetail || "Có thể cần trích xuất lại chủ đề."}
      onLoadKey={extractedPreviewUrl}
    />
  );

  return (
    <section className={`split-review ${isTopicVariant ? "topic-split-review" : ""}`}>
      <div className="split-review-header">
        <div>
          <h3>{title}</h3>
          {description ? <p>{description}</p> : null}
        </div>
        {extractedStatusBadge ? (
          <div className="splitStatusSlot">{extractedStatusBadge}</div>
        ) : null}
      </div>
      {isTopicVariant ? (
        <>
          <div className="previewTabs" role="tablist" aria-label="Chế độ xem PDF">
            <button type="button" className={activePreview === "source" ? "active" : ""} onClick={() => setActivePreview("source")}>Sách gốc</button>
            <button type="button" className={activePreview === "extracted" ? "active" : ""} onClick={() => setActivePreview("extracted")}>Topic đã trích xuất</button>
            <button type="button" className={activePreview === "compare" ? "active" : ""} onClick={() => setActivePreview("compare")}>So sánh</button>
          </div>
          <div className={`split-preview-grid topic-preview-grid mode-${activePreview}`}>
            {activePreview !== "extracted" ? sourceCard : null}
            {activePreview !== "source" ? extractedCard : null}
          </div>
        </>
      ) : (
        <div className="split-preview-grid">
          {sourceCard}
          {extractedCard}
        </div>
      )}
      {children ? <div className="review-metadata-panel">{children}</div> : null}
    </section>
  );
}
