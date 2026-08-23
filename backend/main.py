import base64
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiofiles
import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

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

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_CATEGORY = "咖啡店"

AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
AMAP_AROUND_URL = "https://restapi.amap.com/v3/place/around"
SEARCH_RADIUS_METERS = 1000
SEARCH_POI_LIMIT = 3

EXTRACT_SYSTEM_PROMPT = """你是碰面地点助手的信息抽取模块。用户会用一句话描述两个人的位置，以及想在中间碰面做什么。

你的任务：只从用户原话中提取三个字段，并严格输出一个 JSON 对象，不要输出任何解释、Markdown、代码块标记或其他文字。

JSON 固定格式（字段名必须完全一致）：
{"address_a":"<我的地址>","address_b":"<朋友的地址>","category":"<碰面想做什么>"}

规则：
1. address_a：说话人自己的位置（如「我在…」「我这边…」）。
2. address_b：朋友/对方的位置（如「朋友在…」「他在…」）。
3. category：碰面想做的事或场所类型（如咖啡店、火锅、书店）。若用户完全没提想做什么，category 必须填「咖啡店」。
4. 三个字段的值都用简体中文短短语，不要加多余标点或整句解释。
5. 若原话与约碰面无关，或无法确定两个不同地址，仍输出 JSON，但把缺的地址设为空字符串 ""。
6. 禁止输出 JSON 以外的任何字符。"""


class ExtractRequest(BaseModel):
    text: str = Field(..., min_length=1)


class SearchRequest(BaseModel):
    address_a: str = Field(..., min_length=1)
    address_b: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)


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


def _get_deepseek_api_key() -> str:
    key = (config.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        print("错误：未配置 DEEPSEEK_API_KEY，请在 backend/.env 中填写。")
        raise HTTPException(
            status_code=500,
            detail="信息提取服务未配置，请先在后端 .env 中填写 DEEPSEEK_API_KEY。",
        )
    return key


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_extract_payload(raw_content: str) -> dict[str, str]:
    cleaned = _strip_json_fence(raw_content)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 容错：截取第一个 {...} 再试一次
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("模型未返回 JSON 对象")
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError("模型返回的不是 JSON 对象")

    if "address_a" not in data or "address_b" not in data:
        raise ValueError("缺少 address_a 或 address_b 字段")

    address_a = str(data.get("address_a") or "").strip()
    address_b = str(data.get("address_b") or "").strip()
    category_raw = str(data.get("category") or "").strip() if "category" in data else ""
    category = category_raw or DEFAULT_CATEGORY

    if not address_a and not address_b:
        raise ValueError("两个地址都为空")
    if not address_a or not address_b:
        raise ValueError("只提取到一个地址")

    return {
        "address_a": address_a,
        "address_b": address_b,
        "category": category,
    }


@app.post("/extract")
async def extract(body: ExtractRequest) -> dict[str, str]:
    user_text = body.text.strip()
    if not user_text:
        print("错误：信息提取失败，输入文字为空。")
        raise HTTPException(status_code=400, detail="没有可用的识别文字，请重新录音。")

    api_key = _get_deepseek_api_key()
    request_body: dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=60.0) as client:
            response = await client.post(
                DEEPSEEK_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
    except httpx.TimeoutException:
        print("错误：调用 DeepSeek 信息提取超时。")
        raise HTTPException(status_code=504, detail="信息提取超时，请稍后重试。")
    except httpx.HTTPError as exc:
        print(f"错误：调用 DeepSeek 信息提取网络失败：{exc}")
        raise HTTPException(
            status_code=502,
            detail="信息提取服务暂时不可用，请稍后重试。",
        )

    if response.status_code != 200:
        print(
            f"错误：DeepSeek 信息提取 HTTP 状态异常：{response.status_code}，"
            f"响应片段：{response.text[:500]}"
        )
        raise HTTPException(
            status_code=502,
            detail="信息提取失败，请确认 DeepSeek 密钥有效后重试。",
        )

    try:
        payload = response.json()
        raw_content = payload["choices"][0]["message"]["content"]
        if not isinstance(raw_content, str):
            raise TypeError("content 不是字符串")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"错误：DeepSeek 返回结构异常：{exc}；原始响应：{response.text[:500]}")
        raise HTTPException(
            status_code=502,
            detail="信息提取结果异常，请稍后重试。",
        )

    try:
        result = _parse_extract_payload(raw_content)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"错误：信息提取审核失败：{exc}；模型原文：{raw_content[:500]}")
        message = str(exc)
        if "只提取到一个地址" in message:
            detail = "只听清了一个地址，请再说一次两个人各自在哪里。"
        elif "两个地址都为空" in message:
            detail = "请说清两人和碰面品类，例如：我在A，朋友在B，找个中间的咖啡店。"
        else:
            detail = "没能理解你的碰面信息，请再说一次两个人的位置和想做什么。"
        raise HTTPException(status_code=422, detail=detail)

    return result


def _get_amap_api_key() -> str:
    key = (config.get("AMAP_API_KEY") or "").strip()
    if not key:
        print("错误：未配置 AMAP_API_KEY，请在 backend/.env 中填写。")
        raise HTTPException(
            status_code=500,
            detail="地图服务未配置，请先在后端 .env 中填写 AMAP_API_KEY。",
        )
    return key


def _amap_count(payload: dict) -> int:
    raw = payload.get("count", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def _amap_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    step_label: str,
) -> dict[str, Any]:
    try:
        response = await client.get(url, params=params)
    except httpx.TimeoutException:
        print(f"错误：高德{step_label}网络请求超时。")
        raise HTTPException(status_code=504, detail="查找碰面地点超时，请稍后重试。")
    except httpx.HTTPError as exc:
        print(f"错误：高德{step_label}网络请求失败：{exc}")
        raise HTTPException(
            status_code=502,
            detail="地图服务暂时不可用，请稍后重试。",
        )

    if response.status_code != 200:
        print(
            f"错误：高德{step_label} HTTP 状态异常：{response.status_code}，"
            f"响应片段：{response.text[:300]}"
        )
        raise HTTPException(status_code=502, detail="地图服务调用失败，请稍后重试。")

    try:
        payload = response.json()
    except ValueError:
        print(f"错误：高德{step_label}返回了无法解析的内容。")
        raise HTTPException(status_code=502, detail="地图服务返回异常，请稍后重试。")

    if str(payload.get("status")) != "1":
        info = payload.get("info") or payload.get("infocode") or "未知原因"
        print(
            f"错误：高德{step_label}业务调用失败，status={payload.get('status')}，info={info}"
        )
        raise HTTPException(status_code=502, detail="地图服务调用失败，请稍后重试。")

    return payload


def _parse_geocode_location(
    payload: dict[str, Any],
    *,
    role_label: str,
    address_text: str,
) -> tuple[float, float]:
    count = _amap_count(payload)
    geocodes = payload.get("geocodes") or []
    if count == 0 or not geocodes:
        print(
            f"错误：地址「{address_text}」（{role_label}）地理编码 count 为 0，未能识别该地址。"
        )
        raise HTTPException(
            status_code=422,
            detail="有一个地址没识别出来，换个说法再说说。",
        )

    location = str(geocodes[0].get("location") or "").strip()
    parts = location.split(",")
    if len(parts) != 2:
        print(
            f"错误：地址「{address_text}」（{role_label}）地理编码缺少有效经纬度：{location}"
        )
        raise HTTPException(
            status_code=422,
            detail="有一个地址没识别出来，换个说法再说说。",
        )

    try:
        lng = float(parts[0])
        lat = float(parts[1])
    except ValueError:
        print(
            f"错误：地址「{address_text}」（{role_label}）经纬度无法解析：{location}"
        )
        raise HTTPException(
            status_code=422,
            detail="有一个地址没识别出来，换个说法再说说。",
        )

    return lng, lat


@app.post("/search")
async def search(body: SearchRequest) -> dict[str, Any]:
    address_a = body.address_a.strip()
    address_b = body.address_b.strip()
    category = body.category.strip() or DEFAULT_CATEGORY

    if not address_a or not address_b:
        print("错误：碰面搜索失败，address_a 或 address_b 为空。")
        raise HTTPException(
            status_code=400,
            detail="请说清两人和碰面品类，例如：我在A，朋友在B，找个中间的咖啡店。",
        )

    api_key = _get_amap_api_key()

    async with httpx.AsyncClient(trust_env=False, timeout=20.0) as client:
        geo_a = await _amap_get(
            client,
            AMAP_GEOCODE_URL,
            {"key": api_key, "address": address_a},
            f"地理编码（我的地址：{address_a}）",
        )
        lng_a, lat_a = _parse_geocode_location(
            geo_a,
            role_label="我的地址 address_a",
            address_text=address_a,
        )

        geo_b = await _amap_get(
            client,
            AMAP_GEOCODE_URL,
            {"key": api_key, "address": address_b},
            f"地理编码（朋友地址：{address_b}）",
        )
        lng_b, lat_b = _parse_geocode_location(
            geo_b,
            role_label="朋友地址 address_b",
            address_text=address_b,
        )

        mid_lng = (lng_a + lng_b) / 2
        mid_lat = (lat_a + lat_b) / 2
        location = f"{mid_lng:.6f},{mid_lat:.6f}"

        around = await _amap_get(
            client,
            AMAP_AROUND_URL,
            {
                "key": api_key,
                "location": location,
                "keywords": category,
                "radius": SEARCH_RADIUS_METERS,
                "offset": SEARCH_POI_LIMIT,
                "page": 1,
                "extensions": "base",
            },
            f"周边搜索（品类：{category}，中点：{location}）",
        )

    pois = around.get("pois") or []
    if not isinstance(pois, list) or len(pois) == 0:
        print(
            f"错误：中点周边按品类「{category}」搜索 POI 列表为空"
            f"（中点 {location}，半径 {SEARCH_RADIUS_METERS} 米）。"
        )
        raise HTTPException(
            status_code=422,
            detail="中点附近暂时找不到这类地方，换个品类或再说一次地址试试。",
        )

    places: list[dict[str, str]] = []
    for poi in pois[:SEARCH_POI_LIMIT]:
        if not isinstance(poi, dict):
            continue
        name = str(poi.get("name") or "").strip()
        address = str(poi.get("address") or "").strip()
        if isinstance(poi.get("address"), list):
            address = ""
        if not name:
            continue
        places.append({"name": name, "address": address or "地址暂缺"})

    if not places:
        print(
            f"错误：中点周边 POI 原始列表非空，但有效地点（含名称）为 0。"
            f"品类「{category}」，中点 {location}。"
        )
        raise HTTPException(
            status_code=422,
            detail="中点附近暂时找不到这类地方，换个品类或再说一次地址试试。",
        )

    return {
        "midpoint": {"lng": mid_lng, "lat": mid_lat},
        "places": places,
    }
