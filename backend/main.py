from datetime import datetime
from pathlib import Path

import aiofiles
from dotenv import dotenv_values
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

# 只从 backend/.env 读取配置，不读系统环境变量
_ENV_PATH = Path(__file__).resolve().parent / ".env"
config = dotenv_values(_ENV_PATH)

_BASE_DIR = Path(__file__).resolve().parent
_STORAGE_DIR = _BASE_DIR / "storage"
_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

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
