import { useState } from "react";
import "./App.css";

const DEMO_TRANSCRIPT =
  "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店。";

const DEMO_RESULT = `识别原文：
${DEMO_TRANSCRIPT}

推荐碰面地点（示例）：
1. 星巴克（打铁关店）— 距中点约 320 米
2. 瑞幸咖啡（和平广场店）— 距中点约 450 米
3. 曼咖啡（和平店）— 距中点约 680 米

口播：你们可以约在中河中路附近的星巴克打铁关店，离两人中间位置大约三百米。`;

function App() {
  const [resultText] = useState(DEMO_RESULT);

  function handleRecordClick() {
    window.alert("录音功能下一步实现");
  }

  function handlePlayClick() {
    window.alert("播放功能下一步实现");
  }

  return (
    <div className="page">
      <header className="brand">
        <h1 className="brand-title">语音约碰面</h1>
        <p className="brand-sub">对着麦克风说说两人和想去的地方</p>
      </header>

      <main className="stage">
        <button
          type="button"
          className="record-btn"
          onClick={handleRecordClick}
          aria-label="开始录音"
        >
          <span className="record-btn__core" />
          <span className="record-btn__label">按住说话</span>
        </button>

        <section className="result" aria-label="识别与推荐结果">
          <h2 className="result__heading">识别与推荐</h2>
          <pre className="result__body">{resultText}</pre>
        </section>

        <button type="button" className="play-btn" onClick={handlePlayClick}>
          播放口播
        </button>
      </main>
    </div>
  );
}

export default App;
