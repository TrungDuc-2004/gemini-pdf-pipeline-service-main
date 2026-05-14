export default function LoadingState({ message = "Đang tải dữ liệu..." }) {
  return (
    <div className="stateBox loadingState">
      <span className="spinner" aria-hidden="true" />
      <p>{message}</p>
    </div>
  );
}
