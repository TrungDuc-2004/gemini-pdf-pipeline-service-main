import RawJsonPanel from "../components/RawJsonPanel.jsx";

const COLUMNS = [
  "topic_num",
  "topic_name",
  "start",
  "end",
  "raw_heading",
  "raw_title",
];

export default function TopicReviewView({
  topics,
  approved,
  rawTopicsResponse,
  rawOpen,
  onToggleRaw,
  onChangeTopic,
  onSave,
  onApprove,
  onReload,
  loading,
}) {
  if (topics == null) {
    return (
      <section className="panel">
        <div className="panelHeader">
          <h2>Duyệt chủ đề</h2>
          <button type="button" onClick={onReload} disabled={loading}>
            Tải danh sách chủ đề
          </button>
        </div>
        <p className="muted">
          {loading ? "Đang tải danh sách chủ đề..." : "Chưa tải danh sách chủ đề."}
        </p>
      </section>
    );
  }

  const safeTopics = Array.isArray(topics) ? topics : [];

  return (
    <section className="panel widePanel">
      <div className="panelHeader">
        <div>
          <h2>Duyệt chủ đề</h2>
          <p className="muted">Trạng thái duyệt: <strong>{approved ? "approved=true" : "approved=false"}</strong></p>
        </div>
        <div className="buttonRow compact">
          <button type="button" onClick={onReload} disabled={loading}>Làm mới danh sách</button>
          <button type="button" onClick={onSave} disabled={loading || approved}>Lưu chỉnh sửa</button>
          <button type="button" onClick={onApprove} disabled={loading || approved || safeTopics.length === 0}>Duyệt chủ đề</button>
        </div>
      </div>

      {!Array.isArray(topics) ? (
        <div className="errorBox">Dữ liệu chủ đề không đúng định dạng danh sách.</div>
      ) : null}
      {Array.isArray(topics) && safeTopics.length === 0 ? (
        <p className="muted">Danh sách chủ đề đang trống.</p>
      ) : null}

      <div className="tableWrap">
        <table className="topicTable">
          <thead>
            <tr>
              <th>#</th>
              {COLUMNS.map((column) => <th key={column}>{column}</th>)}
            </tr>
          </thead>
          <tbody>
            {safeTopics.length === 0 ? (
              <tr>
                <td colSpan={COLUMNS.length + 1} className="emptyCell">
                  Không có chủ đề để hiển thị.
                </td>
              </tr>
            ) : null}
            {safeTopics.map((topic, index) => (
              <tr key={topic.name || index}>
                <td className="rowNumber">{index + 1}</td>
                {COLUMNS.map((column) => (
                  <td key={column}>
                    <input
                      type={column === "start" || column === "end" ? "number" : "text"}
                      value={topic[column] ?? ""}
                      disabled={approved}
                      onChange={(event) => {
                        const value = column === "start" || column === "end"
                          ? Number(event.target.value)
                          : event.target.value;
                        onChangeTopic(index, column, value);
                      }}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <RawJsonPanel
        title="JSON chủ đề"
        data={rawTopicsResponse}
        open={rawOpen}
        onToggle={onToggleRaw}
      />
    </section>
  );
}
