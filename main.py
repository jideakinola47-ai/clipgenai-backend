
from typing import Any

import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

    return ext


LANGUAGE_CODES = {
    "auto": "auto",
    "english": "en",
    "en": "en",
    "spanish": "es",
    "es": "es",
    "french": "fr",
    "fr": "fr",
    "german": "de",
    "de": "de",
    "portuguese": "pt",
    "pt": "pt",
    "russian": "ru",
    "ru": "ru",
    "italian": "it",
    "it": "it",
    "dutch": "nl",
    "nl": "nl",
    "arabic": "ar",
    "ar": "ar",
    "hindi": "hi",
    "hi": "hi",
    "indonesian": "id",
    "id": "id",
    "japanese": "ja",
    "ja": "ja",
    "korean": "ko",
    "ko": "ko",
    "mandarin": "zh",
    "chinese": "zh",
    "zh": "zh",
    "turkish": "tr",
    "tr": "tr",
    "ukrainian": "uk",
    "uk": "uk",
    "vietnamese": "vi",
    "vi": "vi",
    # Vizard may auto-detect languages not listed in its API docs.
    "lithuanian": "auto",
    "lt": "auto",
    "polish": "auto",
    "pl": "auto",
    "swedish": "auto",
    "norwegian": "auto",
    "danish": "auto",
    "finnish": "auto",
}


def normalize_language(value: Any) -> str:
    key = str(value or "auto").strip().lower()
    return LANGUAGE_CODES.get(key, "auto")


def int_from_form(value: Any, default: int) -> int:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    match = re.search(r"\d+", text)
    if not match:
        return default
    try:
        return int(match.group(0))
    except ValueError:
        return default


def prefer_length_from_form(value: Any) -> int:
    text = str(value or "").strip().lower()
    if "30" in text and "60" in text:
        return 2
    if "60" in text and "90" in text:
        return 3
    if "90" in text or "3min" in text or "3 min" in text:
        return 4
    number = int_from_form(value, 2)
    return number if number in {0, 1, 2, 3, 4} else 2


def bool_int_from_form(value: Any, default: int = 1) -> int:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"0", "false", "off", "no", "disabled"}:
        return 0
    return 1


def vizard_headers() -> dict[str, str]:
    if not VIZARD_API_KEY:
        raise HTTPException(status_code=500, detail="VIZARD_API_KEY is missing on the server.")


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
async def upload_video(request: Request) -> dict[str, Any]:
    if not BASE_URL:
        raise HTTPException(status_code=500, detail="BASE_URL is missing on the server.")

    form = await request.form()
    file = None
    for key in ("file", "video", "upload", "media"):
        candidate = form.get(key)
        if isinstance(candidate, UploadFile):
            file = candidate
            break

    if file is None:
        raise HTTPException(
            status_code=400,
            detail="No video file found in upload. Expected form field named file or video.",
        )

    language = normalize_language(
        form.get("language")
        or form.get("subtitleLanguage")
        or form.get("subtitle_language")
        or form.get("lang")
        or "auto"
    )
    prefer_length = prefer_length_from_form(
        form.get("prefer_length") or form.get("preferLength") or form.get("clipLength") or 2
    )
    max_clips = int_from_form(form.get("max_clips") or form.get("maxClipNumber"), 8)
    subtitle_switch = bool_int_from_form(
        form.get("subtitle_switch") or form.get("subtitleSwitch") or form.get("captions"), 1
    )
    headline_switch = bool_int_from_form(form.get("headline_switch") or form.get("headlineSwitch"), 1)
    ratio = int_from_form(form.get("ratio") or form.get("ratioOfClip"), 1)

    ext = ext_from_filename(file.filename or "video.mp4")
    filename = clean_filename(file.filename or "video.mp4")
    path = UPLOAD_DIR / filename

    video_url = f"{BASE_URL}/files/{filename}"
    payload = {
        "lang": language or "auto",
        "lang": language,
        "preferLength": [prefer_length] if prefer_length in {1, 2, 3, 4} else [0],
        "videoUrl": video_url,
        "videoType": 1,
