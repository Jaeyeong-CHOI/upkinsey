import React from "react";
import { createRoot } from "react-dom/client";
import App from "./app.jsx";
import "./styles.css";

class AppErrorBoundary extends React.Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="shell page" role="alert">
          <h1 className="page-title">화면을 표시하지 못했어요</h1>
          <p className="page-sub">페이지를 새로고침한 뒤 다시 시도해주세요. 문제가 계속되면 오류가 발생한 화면과 재현 방법을 알려주세요.</p>
          <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>새로고침</button>
          <a className="btn" href="https://github.com/Jaeyeong-CHOI/upkinsey/issues">문제 신고</a>
        </main>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("app")).render(
  <AppErrorBoundary><App /></AppErrorBoundary>
);
