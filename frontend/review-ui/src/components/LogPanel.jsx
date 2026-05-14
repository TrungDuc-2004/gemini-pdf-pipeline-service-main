export default function LogPanel({ log, onRefresh, loading }) {
  async function copyLog() {
    if (!navigator.clipboard) return;
    await navigator.clipboard.writeText(log || "");
  }

  return (
    <section className="panel inspectorCard">
      <div className="panelHeader">
        <div>
          <h2>Nhật ký</h2>
          <p className="muted">Theo dõi pipeline và lỗi xử lý.</p>
        </div>
        <div className="buttonRow compact">
          <button type="button" onClick={copyLog} disabled={!log}>Copy</button>
          <button type="button" onClick={onRefresh} disabled={loading}>{loading ? "Đang tải..." : "Làm mới"}</button>
        </div>
      </div>
      <pre className="logBox">{log || "Chưa có log."}</pre>
    </section>
  );
}
