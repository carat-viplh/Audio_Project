import base64
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiofiles
import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

# 只从 backend/.env 读取配置，不读系统环境变量
_ENV_PATH = Path(__file__).resolve().parent / ".env"
config = dotenv_values(_ENV_PATH)

_BASE_DIR = Path(__file__).resolve().parent
_STORAGE_DIR = _BASE_DIR / "storage"
_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

BAILIAN_ASR_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
ASR_MODEL = "qwen3-asr-flash"

app = FastAPI(title="语音约碰面后端")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "语音约碰面后端已启动"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, object]:
    original_name = file.filename or "recording.webm"
    suffix = Path(original_name).suffix or ".webm"
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{suffix}"
    dest = _STORAGE_DIR / filename

    async with aiofiles.open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            await out.write(chunk)

    return {"success": True, "filename": filename}


def _get_bailian_api_key() -> str:
    key = (config.get("BAILIAN_API_KEY") or "").strip()
    if not key:
        print("错误：未配置 BAILIAN_API_KEY，请在 backend/.env 中填写。")
        raise HTTPException(
            status_code=500,
            detail="语音识别服务未配置，请先在后端 .env 中填写 BAILIAN_API_KEY。",
        )
    return key


def _guess_audio_mime(filename: Optional[str]) -> str:
    suffix = Path(filename or "recording.webm").suffix.lower()
    mapping = {
        ".webm": "audio/webm",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
    }
    return mapping.get(suffix, "audio/webm")


def _extract_asr_text(payload: dict) -> str:
    """从百炼 ASR 多种可能返回结构中取出识别文本。"""
    output = payload.get("output") or {}
    choices = output.get("choices") or payload.get("choices") or []

    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                elif isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]).strip())
            return "".join(parts).strip()

    if isinstance(output.get("text"), str):
        return output["text"].strip()

    nested = output.get("output") or {}
    sentence = nested.get("sentence") or {}
    if isinstance(sentence.get("text"), str):
        return sentence["text"].strip()

    return ""


@app.post("/asr")
async def asr(file: UploadFile = File(...)) -> dict[str, str]:
    api_key = _get_bailian_api_key()
    audio_bytes = await file.read()
    if not audio_bytes:
        print("错误：语音识别失败，上传的音频内容为空。")
        raise HTTPException(
            status_code=400,
            detail="没有听到有效音频，请重新录音后再试。",
        )

    mime = _guess_audio_mime(file.filename)
    data_uri = f"data:{mime};base64,{base64.b64encode(audio_bytes).decode('ascii')}"
    body = {
        "model": ASR_MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"audio": data_uri}],
                }
            ]
        },
        "parameters": {
            "asr_options": {
                "language": "zh",
                "enable_itn": False,
            }
        },
    }

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=60.0) as client:
            response = await client.post(
                BAILIAN_ASR_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException:
        print("错误：调用百炼语音识别超时。")
        raise HTTPException(status_code=504, detail="语音识别超时，请稍后重试。")
    except httpx.HTTPError as exc:
        print(f"错误：调用百炼语音识别网络失败：{exc}")
        raise HTTPException(
            status_code=502,
            detail="语音识别服务暂时不可用，请稍后重试。",
        )

    if response.status_code != 200:
        print(
            f"错误：百炼语音识别 HTTP 状态异常：{response.status_code}，"
            f"响应片段：{response.text[:500]}"
        )
        raise HTTPException(
            status_code=502,
            detail="语音识别失败，请确认密钥有效后重试。",
        )

    try:
        payload = response.json()
    except ValueError:
        print("错误：百炼语音识别返回了无法解析的内容。")
        raise HTTPException(
            status_code=502,
            detail="语音识别结果异常，请稍后重试。",
        )

    if payload.get("code") and not payload.get("output"):
        print(f"错误：百炼语音识别业务失败：{payload}")
        raise HTTPException(
            status_code=502,
            detail="语音识别失败，请确认密钥与模型权限后重试。",
        )

    text = _extract_asr_text(payload)
    if not text:
        print(f"错误：百炼语音识别结果为空。原始返回：{payload}")
        raise HTTPException(
            status_code=422,
            detail="没有听清你说的话，请靠近麦克风再说一次。",
        )

    return {"text": text}
