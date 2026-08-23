from pathlib import Path

from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

# 只从 backend/.env 读取配置，不读系统环境变量
_ENV_PATH = Path(__file__).resolve().parent / ".env"
config = dotenv_values(_ENV_PATH)

app = FastAPI(title="语音约碰面后端")


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "语音约碰面后端已启动"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
