import { useEffect, useRef, useState } from "react";
import axios from "axios";
import "./App.css";

const API_BASE = "http://localhost:8003";

const DEMO_TRANSCRIPT =
  "我在杭州东站，朋友在西湖龙翔桥地铁站，帮我们找个中间的咖啡店。";

const DEMO_RESULT = `识别原文：
${DEMO_TRANSCRIPT}

推荐碰面地点（示例）：
1. 星巴克（打铁关店）— 距中点约 320 米
2. 瑞幸咖啡（和平广场店）— 距中点约 450 米
3. 曼咖啡（和平店）— 距中点约 680 米

口播：你们可以约在中河中路附近的星巴克打铁关店，离两人中间位置大约三百米。`;

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "--";
  const totalMs = Math.round(seconds * 1000);
  const m = Math.floor(totalMs / 60000);
  const s = Math.floor((totalMs % 60000) / 1000);
  const ms = totalMs % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "--";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function pickWebmMimeType() {
  const candidates = ["audio/webm;codecs=opus", "audio/webm"];
  for (const type of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type)) {
      return type;
    }
  }
  return "";
}

function createRecorder(stream) {
  const mimeType = pickWebmMimeType();
  try {
    return mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);
  } catch {
    return new MediaRecorder(stream);
  }
}

function App() {
  const [resultText, setResultText] = useState(DEMO_RESULT);
  const [isRecording, setIsRecording] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [audioMeta, setAudioMeta] = useState(null);

  const mediaRecorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const startedAtRef = useRef(0);
  const audioUrlRef = useRef("");
  const stoppingRef = useRef(false);
  const wantRecordingRef = useRef(false);
  const startingRef = useRef(false);

  useEffect(() => {
    return () => {
      wantRecordingRef.current = false;
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.stop();
      }
      streamRef.current?.getTracks().forEach((track) => track.stop());
      if (audioUrlRef.current) {
        URL.revokeObjectURL(audioUrlRef.current);
      }
    };
  }, []);

  function revokeCurrentUrl() {
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = "";
    }
  }

  function stopStream() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }

  async function uploadAudio(file) {
    setStatusText("正在上传到后端…");
    try {
      const uploadForm = new FormData();
      uploadForm.append("file", file);

      const { data: uploadData } = await axios.post(
        `${API_BASE}/upload`,
        uploadForm
      );

      if (!(uploadData?.success && uploadData?.filename)) {
        setStatusText("上传失败：后端未返回成功状态");
        return;
      }

      setStatusText("正在识别语音…");
      const asrForm = new FormData();
      asrForm.append("file", file);

      const { data: asrData } = await axios.post(`${API_BASE}/asr`, asrForm);
      const text = typeof asrData?.text === "string" ? asrData.text.trim() : "";

      if (!text) {
        setStatusText("识别失败：未得到文字");
        setResultText("没有听清你说的话，请靠近麦克风再说一次。");
        return;
      }

      setResultText(`识别原文：\n${text}`);
      setStatusText("正在提取碰面信息…");

      const { data: extractData } = await axios.post(`${API_BASE}/extract`, {
        text,
      });

      const addressA =
        typeof extractData?.address_a === "string" ? extractData.address_a.trim() : "";
      const addressB =
        typeof extractData?.address_b === "string" ? extractData.address_b.trim() : "";
      const category =
        typeof extractData?.category === "string" ? extractData.category.trim() : "";

      if (!addressA || !addressB || !category) {
        setStatusText("信息提取失败：字段不完整");
        setResultText(
          `识别原文：\n${text}\n\n没能完整提取碰面信息，请再说一次两个人的位置和想做什么。`
        );
        return;
      }

      setResultText(
        `识别原文：\n${text}\n\n我的地址：${addressA}\n朋友地址：${addressB}\n碰面想做：${category}`
      );
      setStatusText("信息提取完成");
    } catch (err) {
      const detail = err?.response?.data?.detail;
      if (typeof detail === "string" && detail.trim()) {
        setStatusText(detail);
        setResultText(detail);
        return;
      }

      const url = err?.config?.url || "";
      if (String(url).includes("/extract")) {
        setStatusText("信息提取失败，请稍后重试");
        setResultText("信息提取失败，请稍后重试");
      } else if (String(url).includes("/asr")) {
        setStatusText("语音识别失败，请稍后重试");
        setResultText("语音识别失败，请稍后重试");
      } else {
        setStatusText("上传失败，请确认后端已在 8003 端口启动");
      }
    }
  }

  async function startRecording(event) {
    event.preventDefault();
    if (event.button != null && event.button !== 0) return;

    wantRecordingRef.current = true;

    if (isRecording || mediaRecorderRef.current || startingRef.current) {
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      wantRecordingRef.current = false;
      setStatusText("当前浏览器不支持录音，请换用较新的 Chrome / Edge / Firefox。");
      return;
    }

    startingRef.current = true;
    setStatusText("正在请求麦克风权限…");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // 授权弹窗期间用户往往已经松开：此时不要偷偷开录，引导再按一次
      if (!wantRecordingRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        setStatusText("麦克风已就绪，请再次按住说话开始录音");
        return;
      }

      streamRef.current = stream;
      const recorder = createRecorder(stream);
      const mimeType = recorder.mimeType || "audio/webm";
      chunksRef.current = [];
      stoppingRef.current = false;

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        const elapsedSec = (performance.now() - startedAtRef.current) / 1000;
        const blob = new Blob(chunksRef.current, {
          type: mimeType.includes("webm") ? mimeType : "audio/webm",
        });
        const file = new File([blob], `recording-${Date.now()}.webm`, {
          type: "audio/webm",
        });

        revokeCurrentUrl();
        const url = URL.createObjectURL(file);
        audioUrlRef.current = url;
        setAudioUrl(url);
        setAudioMeta({
          durationSec: elapsedSec,
          sizeBytes: file.size,
          fileName: file.name,
        });
        setIsRecording(false);
        mediaRecorderRef.current = null;
        stopStream();
        uploadAudio(file);
      };

      // 启动前再确认一次意图，避免 await 后瞬间松手
      if (!wantRecordingRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        setStatusText("麦克风已就绪，请再次按住说话开始录音");
        return;
      }

      mediaRecorderRef.current = recorder;
      startedAtRef.current = performance.now();
      recorder.start();
      setIsRecording(true);
      setStatusText("录制中…松开结束");

      if (event.currentTarget?.setPointerCapture && event.pointerId != null) {
        try {
          event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
          // 部分情况下 capture 会失败，不影响录音本身
        }
      }
    } catch (err) {
      wantRecordingRef.current = false;
      stopStream();
      setIsRecording(false);
      mediaRecorderRef.current = null;
      if (err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError") {
        setStatusText("无法使用麦克风，请在浏览器设置中允许本站使用麦克风。");
      } else {
        setStatusText("无法开始录音，请检查麦克风后重试。");
      }
    } finally {
      startingRef.current = false;
    }
  }

  function stopRecording(event) {
    event?.preventDefault?.();
    wantRecordingRef.current = false;

    if (stoppingRef.current) return;
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state !== "recording") {
      // 仍在申请权限 / 尚未 start：只清意图，等 startRecording 里的检查收尾
      return;
    }
    stoppingRef.current = true;
    recorder.stop();
  }

  function handlePlayClick() {
    window.alert("播放功能下一步实现");
  }

  function handleAudioLoadedMetadata(event) {
    const duration = event.currentTarget.duration;
    if (!Number.isFinite(duration) || duration === Infinity) return;
    setAudioMeta((prev) =>
      prev ? { ...prev, durationSec: duration } : prev
    );
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
          className={`record-btn${isRecording ? " record-btn--recording" : ""}`}
          onPointerDown={startRecording}
          onPointerUp={stopRecording}
          onPointerCancel={stopRecording}
          onLostPointerCapture={stopRecording}
          onContextMenu={(e) => e.preventDefault()}
          aria-label={isRecording ? "松开结束录音" : "按住开始录音"}
        >
          <span className="record-btn__core" />
          <span className="record-btn__label">
            {isRecording ? "松开结束" : "按住说话"}
          </span>
        </button>

        {statusText ? <p className="status-text">{statusText}</p> : null}

        {audioMeta && audioUrl ? (
          <section className="recording-preview" aria-label="录音预览">
            <h2 className="result__heading">本次录音</h2>
            <p className="recording-preview__meta">
              时长 {formatDuration(audioMeta.durationSec)} · 大小{" "}
              {formatSize(audioMeta.sizeBytes)} · {audioMeta.fileName}
            </p>
            <audio
              className="recording-preview__player"
              controls
              src={audioUrl}
              onLoadedMetadata={handleAudioLoadedMetadata}
            />
          </section>
        ) : null}

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
