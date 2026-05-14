import { Component } from "react";

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    this.setState({ info });
    console.error("Review UI render error", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="errorBoundary">
          <h1>Ứng dụng gặp lỗi khi hiển thị</h1>
          <p>Vui lòng kiểm tra console trình duyệt để xem chi tiết runtime.</p>
          <pre>{this.state.error?.message || String(this.state.error)}</pre>
          {this.state.info?.componentStack ? <pre>{this.state.info.componentStack}</pre> : null}
        </main>
      );
    }

    return this.props.children;
  }
}
