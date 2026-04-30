import os, uuid, asyncio, httpx, shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"])

VIZARD_API_KEY = os.getenv("VIZARD_API_KEY", "d3d058e542074fa89cd861a18c6555d5")
VIZARD_CREATE  = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/create"
VIZARD_QUERY   = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/query/{}"
BASE_URL       = os.getenv("BASE_URL", "https://web-production-189e9.up.railway.app")

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/files", StaticFiles(directory="uploads"), name="files")

# In-memory job store
jobs = {}

LANG_MAP = {
    "English": "en", "Lithuanian (Lietuvių)": "lt", "German (Deutsch)": "de",
    "French (Français)": "fr", "Spanish (Español)": "es", "Polish (Polski)": "pl",
    "Russian (Русский)": "ru", "Italian (Italiano)": "it", "Portuguese (Português)": "pt",
    "Dutch (Nederlands)": "nl", "Swedish (Svenska)": "sv", "Norwegian (Norsk)": "no",
    "Danish (Dansk)": "da", "Finnish (Suomi)": "fi", "Japanese (日本語)": "ja",
    "Chinese (简体中文)": "zh", "Korean (한국어)": "ko", "Arabic (العربية)": "ar",
    "Turkish (Türkçe)": "tr", "Hindi (हिन्दी)": "hi",
}

@app.get("/health")
def health():
    return {"status": "ok", "engine": "vizard"}

@app.post("/upload")
async def upload(file: UploadFile = File(...), subtitle_language: str = Form("English"), style: str = Form("")):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "uploading", "progress": 5, "clips": []}

    # Save file
    ext = file.filename.split(".")[-1] if "." in file.filename else "mp4"
    fname = f"{job_id}.{ext}"
    fpath = UPLOAD_DIR / fname
    with open(fpath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    jobs[job_id] = {"status": "uploading", "progress": 20}

    # Get public URL for this file
    video_url = f"{BASE_URL}/files/{fname}"
    lang_code = LANG_MAP.get(subtitle_language, "en")

    # Start Vizard processing in background
    asyncio.create_task(process_with_vizard(job_id, video_url, ext, lang_code))

    return {"job_id": job_id}

async def process_with_vizard(job_id: str, video_url: str, ext: str, lang: str):
    try:
        jobs[job_id] = {"status": "transcribing", "progress": 30}

        headers = {
            "Content-Type": "application/json",
            "VIZARDAI_API_KEY": VIZARD_API_KEY
        }

        payload = {
            "videoUrl": video_url,
            "videoType": 1,  # 1 = direct file URL
            "ext": ext,
            "lang": lang,
            "preferLength": [1, 2],   # 30-60s and 60-90s clips
            "ratioOfClip": 1,          # 9:16 vertical
            "subtitleSwitch": 1,       # auto subtitles on
            "headlineSwitch": 1,       # hook title on
            "maxClipNumber": 10,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(VIZARD_CREATE, headers=headers, json=payload)
            data = res.json()

        print(f"Vizard create response: {data}")
        if data.get("code") != 2000:
            jobs[job_id] = {"status": "failed", "error": f"Vizard error code {data.get('code')}: {data.get('message', str(data))}"}
            return

        project_id = data["projectId"]
        jobs[job_id] = {"status": "scoring", "progress": 50, "project_id": project_id}

        # Poll for results
        for attempt in range(120):
            await asyncio.sleep(5)
            async with httpx.AsyncClient(timeout=30) as client:
                res = await client.get(VIZARD_QUERY.format(project_id), headers={"VIZARDAI_API_KEY": VIZARD_API_KEY})
                result = res.json()

            code = result.get("code")
            if code == 2000:
                # Done — extract clips
                clips = []
                for item in result.get("data", {}).get("videos", []):
                    clips.append({
                        "id": str(item.get("id", "")),
                        "title": item.get("headline") or item.get("name", f"Clip {len(clips)+1}"),
                        "score": item.get("viralScore") or item.get("viral_score") or 80,
                        "duration": item.get("duration", 45),
                        "download_url": item.get("videoUrl") or item.get("video_url", ""),
                        "stream_url": item.get("videoUrl") or item.get("video_url", ""),
                        "thumbnail": item.get("coverUrl") or item.get("cover_url", ""),
                    })
                jobs[job_id] = {"status": "done", "progress": 100, "clips": clips}
                return

            elif code == 2001:
                # Still processing
                progress = min(50 + attempt, 90)
                jobs[job_id] = {"status": "cutting", "progress": progress, "project_id": project_id}
                continue

            else:
                print(f"Vizard poll error: code={code}, response={result}")
                jobs[job_id] = {"status": "failed", "error": f"Vizard code {code}: {result.get('message', str(result)[:200])}"}
                return

        jobs[job_id] = {"status": "failed", "error": "Timed out waiting for Vizard"}

    except Exception as e:
        jobs[job_id] = {"status": "failed", "error": str(e)}

@app.get("/status/{job_id}")
def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job

@app.get("/test-vizard")
async def test_vizard():
    """Test Vizard API connectivity from Railway"""
    import httpx
    headers = {"VIZARDAI_API_KEY": VIZARD_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(
                "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/query/1",
                headers=headers
            )
        return {"status_code": res.status_code, "response": res.text[:300]}
    except Exception as e:
        return {"error": str(e)}
