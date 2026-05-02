import hashlib
import os
import time
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

VIZARD_KEY = os.getenv("VIZARD_API_KEY", "76f3b8d194804562a7fb22584dbd2361")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "de5jdqth5")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "725389195426886")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "pOsrfuxBPy8JmiVfSIbmNz_b6s0")


def create_cloudinary_signature(params: dict) -> str:
    params_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
    return hashlib.sha1(f"{params_str}{CLOUDINARY_API_SECRET}".encode("utf-8")).hexdigest()


@app.get("/cloudinary-sign")
def cloudinary_sign():
    timestamp = int(time.time())
    return {
        "cloud_name": CLOUDINARY_CLOUD_NAME,
        "api_key": CLOUDINARY_API_KEY,
        "timestamp": timestamp,
        "signature": create_cloudinary_signature({"timestamp": timestamp}),
    }


# ✅ FIXED: No external request in health — was causing hang
@app.get("/health")
def health():
    return {"status": "ok", "vizard_key_set": bool(VIZARD_KEY)}


@app.post("/process-video")
async def process_video(data: dict):
    video_url = data.get("secure_url")
    print("Video URL:", video_url)

    if not video_url:
        return {"error": "Missing video URL"}

    # Cloudinary direct download URL for Vizard
    direct_url = video_url.replace("/upload/", "/upload/fl_attachment/")
    print("Sending to Vizard:", direct_url)

    headers = {
        "Content-Type": "application/json",
        "VIZARDAI_API_KEY": VIZARD_KEY
    }

    payload = {
        "videoUrl": direct_url,
        "videoType": 1,
        "ext": "mp4",
        "lang": "en",
        "preferLength": [1],
        "ratioOfClip": 1,
        "subtitleSwitch": 1,
        "maxClipNumber": 5
    }

    print("Payload:", payload)

    try:
        res = requests.post(
            "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/create",
            headers=headers,
            json=payload,
            timeout=60
        )
        print("Vizard status:", res.status_code)
        print("Vizard response:", res.text)

        vizard_data = res.json()

        if vizard_data.get("code") != 2000:
            return {"error": vizard_data.get("errMsg"), "code": vizard_data.get("code")}

        return {"projectId": vizard_data.get("projectId")}

    except requests.exceptions.Timeout:
        print("Vizard API timeout!")
        return {"error": "Vizard API timeout — try again"}
    except Exception as e:
        print("Backend crash:", str(e))
        return {"error": str(e)}


@app.get("/project-status/{project_id}")
def project_status(project_id: int):
    headers = {"VIZARDAI_API_KEY": VIZARD_KEY}
    try:
        response = requests.get(
            f"https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/query/{project_id}",
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Vizard status check failed: {str(e)}")
    