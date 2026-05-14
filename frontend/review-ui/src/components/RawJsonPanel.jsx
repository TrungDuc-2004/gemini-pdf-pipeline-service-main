export default function RawJsonPanel({ title = "JSON gốc", data, open, onToggle }) {
  return (
    <section className="panel inspectorCard">
      <div className="panelHeader">
        <div>
          <h2>{title}</h2>
          <p className="muted">Dữ liệu debug từ API hiện tại.</p>
        </div>
        <button type="button" onClick={onToggle}>
          {open ? "Ẩn JSON" : "Xem JSON gốc"}
        </button>
      </div>
      {open ? <pre className="jsonBox">{JSON.stringify(data, null, 2)}</pre> : null}
    </section>
  );
}
