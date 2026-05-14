export default function EmptyState({ title, message, action }) {
  return (
    <div className="stateBox emptyState">
      <h3>{title || "Không có dữ liệu"}</h3>
      <p>{message}</p>
      {action ? <div className="stateAction">{action}</div> : null}
    </div>
  );
}
