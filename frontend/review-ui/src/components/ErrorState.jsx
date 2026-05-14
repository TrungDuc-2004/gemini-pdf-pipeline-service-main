import { API_BASE_URL } from "../api/reviewApi.js";

export default function ErrorState({ message, onRetry }) {
  return (
    <div className="stateBox errorState">
      <h3>Có lỗi xảy ra</h3>
      <p>{message || `Không tải được dữ liệu. Kiểm tra backend tại ${API_BASE_URL}`}</p>
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          Thử lại
        </button>
      ) : null}
    </div>
  );
}
