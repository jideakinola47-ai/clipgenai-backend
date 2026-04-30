import os
import re
import uuid
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


VIZARD_CREATE_URL = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/create"
VIZARD_QUERY_URL = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/query/{project_id}"

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
VIZARD_API_KEY = os.getenv("VIZARD_API_KEY", "")
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="ClipGen.AI Vizard Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


STATUS_MESSAGES = {
    1000: "Vizard is still processing the video.",
    2000: "Clips generated successfully.",
    4001: "Invalid Vizard API key.",
    4002: "Vizard clipping failed.",
    4003: "Vizard rate limit exceeded.",
    4004: "Unsupported video format.",
    4005: "Invalid video URL or video length issue.",
    4006: "Illegal Vizard API parameter.",
    4007: "Insufficient Vizard account time or credits.",
    4008: "Vizard failed to download the uploaded video.",
}


def clean_filename(name: str) -> str:
    stem = Path(name).stem or "video"
    suffix = Path(name).suffix.lower() or ".mp4"
    safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-")[:80] or "video"
    return f"{uuid.uuid4().hex}_{safe_stem}{suffix}"


def ext_from_filename(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    if ext not in {"mp4", "mov", "avi", "3gp"}:
        raise HTTPException(status_code=400, detail="Unsupported video format. Use mp4, mov, avi, or 3gp.")
    return ext


def vizard_headers() -> dict[str, str]:
    if not VIZARD_API_KEY:
        raise HTTPException(status_code=500, detail="VIZARD_API_KEY is missing on the server.")
    return {
        "Content-Type": "application/json",
        "VIZARDAI_API_KEY": VIZARD_API_KEY,
    }


def score_to_percent(value: Any) -> int:
    try:
        score = float(value or 0)
    except (TypeError, ValueError):
        score = 0
    if score <= 10:
        score *= 10
    return max(0, min(100, round(score)))


def normalize_clip(item: dict[str, Any], index: int) -> dict[str, Any]:
    duration_ms = item.get("videoMsDuration") or item.get("durationMs") or 0
    try:
        duration_seconds = round(float(duration_ms) / 1000)
    except (TypeError, ValueError):
        duration_seconds = 0

    video_url = item.get("videoUrl") or item.get("url") or item.get("downloadUrl") or ""
    title = item.get("title") or f"Clip {index + 1}"

    return {
        "id": str(item.get("videoId") or item.get("id") or index + 1),
        "title": title,
        "score": score_to_percent(item.get("viralScore")),
        "viral_score": score_to_percent(item.get("viralScore")),
        "duration": duration_seconds,
        "duration_seconds": duration_seconds,
        "url": video_url,
        "video_url": video_url,
        "download_url": video_url,
        "thumbnail_url": item.get("thumbnailUrl") or item.get("coverUrl") or "",
        "reason": item.get("viralReason") or "",
        "viral_reason": item.get("viralReason") or "",
        "transcript": item.get("transcript") or "",
        "editor_url": item.get("clipEditorUrl") or "",
        "raw": item,
    }


def normalize_vizard_response(data: dict[str, Any]) -> dict[str, Any]:
    code = data.get("code")
    videos = data.get("videos") or []

    if code == 1000:
        return {
            "status": "processing",
            "progress": 75,
            "message": STATUS_MESSAGES[1000],
            "raw": data,
        }

    if code == 2000 and videos:
        clips = [normalize_clip(item, index) for index, item in enumerate(videos)]
        return {
            "status": "completed",
            "progress": 100,
            "clips": clips,
            "results": clips,
            "project_id": data.get("projectId"),
            "project_name": data.get("projectName"),
            "share_link": data.get("shareLink"),
            "raw": data,
        }

    if code == 2000:
        return {
            "status": "processing",
            "progress": 90,
            "message": "Vizard finished the project but clips are not available yet. Poll again shortly.",
            "raw": data,
        }

    return {
        "status": "failed",
        "error": data.get("errMsg") or STATUS_MESSAGES.get(code, f"Unknown Vizard response code: {code}"),
        "code": code,
        "raw": data,
    }


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok", "engine": "vizard"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "engine": "vizard"}


@app.get("/files/{filename}")
def get_file(filename: str) -> FileResponse:
    path = (UPLOAD_DIR / filename).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path)


@app.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    prefer_length: int = Form(2),
    max_clips: int = Form(8),
    subtitle_switch: int = Form(1),
    headline_switch: int = Form(1),
    ratio: int = Form(1),
) -> dict[str, Any]:
    if not BASE_URL:
        raise HTTPException(status_code=500, detail="BASE_URL is missing on the server.")

    ext = ext_from_filename(file.filename or "video.mp4")
    filename = clean_filename(file.filename or "video.mp4")
    path = UPLOAD_DIR / filename

    with path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)

    video_url = f"{BASE_URL}/files/{filename}"
    payload = {
        "lang": language or "auto",
        "preferLength": [prefer_length] if prefer_length in {1, 2, 3, 4} else [0],
        "videoUrl": video_url,
        "videoType": 1,
        "ext": ext,
        "ratioOfClip": ratio,
        "subtitleSwitch": 1 if subtitle_switch else 0,
        "headlineSwitch": 1 if headline_switch else 0,
        "maxClipNumber": max(1, min(int(max_clips or 8), 20)),
        "projectName": Path(file.filename or filename).stem[:80],
    }

    try:
        response = requests.post(
            VIZARD_CREATE_URL,
            headers=vizard_headers(),
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Vizard create request failed: {exc}") from exc

    if data.get("code") != 2000 or not data.get("projectId"):
        raise HTTPException(
            status_code=502,
            detail={
                "message": data.get("errMsg") or STATUS_MESSAGES.get(data.get("code"), "Vizard did not create a project."),
                "vizard_response": data,
            },
        )

    project_id = str(data["projectId"])
    return {
        "status": "processing",
        "job_id": project_id,
        "id": project_id,
        "project_id": project_id,
        "message": "Video accepted by Vizard. Poll /status/{project_id} for clips.",
        "video_url": video_url,
        "vizard_response": data,
    }


@app.get("/status/{project_id}")
def get_status(project_id: str) -> dict[str, Any]:
    try:
        response = requests.get(
            VIZARD_QUERY_URL.format(project_id=project_id),
            headers=vizard_headers(),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Vizard query request failed: {exc}") from exc

    normalized = normalize_vizard_response(data)
    normalized["job_id"] = str(project_id)
    return normalized


@app.get("/debug-project/{project_id}")
def debug_project(project_id: str) -> dict[str, Any]:
    try:
        response = requests.get(
            VIZARD_QUERY_URL.format(project_id=project_id),
            headers=vizard_headers(),
            timeout=30,
        )
        raw = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Debug query failed: {exc}") from exc
    return {
        "http_status": response.status_code,
        "normalized": normalize_vizard_response(raw),
        "raw": raw,
    }
