import os, uuid, asyncio, httpx, shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

VIZARD_API_KEY = os.getenv("VIZARD_API_KEY", "76f3b8d194804562a7fb22584dbd2361")
VIZARD_CREATE  = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/create"
VIZARD_QUERY   = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/query/{}"
BASE_URL       = os.getenv("BASE_URL", "https://web-production-189e9.up.railway.app")

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/files", StaticFiles(directory="uploads"), name="files")

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

@app.get("/test-vizard")
async def test_vizard():
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(VIZARD_QUERY.format(1), headers={"VIZARDAI_API_KEY": VIZARD_API_KEY})
    return {"status_code": res.status_code, "response": res.text[:200]}

@app.post("/upload")
async def upload(file: UploadFile = File(...), subtitle_language: str = Form("English"), style: str = Form("")):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "uploading", "progress": 10}

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "mp4"
    fname = f"{job_id}.{ext}"
    with open(UPLOAD_DIR / fname, "wb") as f:
        shutil.copyfileobj(file.file, f)

    print(f"Saved {fname}")
    asyncio.create_task(process_with_vizard(
        job_id,
        f"{BASE_URL}/files/{fname}",
        ext,
        LANG_MAP.get(subtitle_language, "en")
    ))
    return {"job_id": job_id}

async def process_with_vizard(job_id: str, video_url: str, ext: str, lang: str):
    try:
        jobs[job_id] = {"status": "transcribing", "progress": 25}
        headers = {"Content-Type": "application/json", "VIZARDAI_API_KEY": VIZARD_API_KEY}
        payload = {
            "videoUrl": video_url, "videoType": 1, "ext": ext, "lang": lang,
            "preferLength": [1, 2], "ratioOfClip": 1,
            "subtitleSwitch": 1, "headlineSwitch": 1, "maxClipNumber": 10,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            res = await client.post(VIZARD_CREATE, headers=headers, json=payload)

        print(f"Create: {res.status_code} {res.text[:300]}")
        if res.status_code != 200:
            jobs[job_id] = {"status": "failed", "error": f"HTTP {res.status_code}: {res.text[:200]}"}
            return

        data = res.json()
        if data.get("code") != 2000:
            jobs[job_id] = {"status": "failed", "error": f"Code {data.get('code')}: {data.get('errMsg', str(data)[:200])}"}
            return

        project_id = data["projectId"]
        print(f"Project ID: {project_id}")
        jobs[job_id] = {"status": "scoring", "progress": 40}

        for attempt in range(150):
            await asyncio.sleep(6)
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    poll = await client.get(VIZARD_QUERY.format(project_id), headers={"VIZARDAI_API_KEY": VIZARD_API_KEY})
                result = poll.json()
                code = result.get("code")
                print(f"Poll {attempt}: code={code}")

                if code == 2000:
                    # EXACT field names from Vizard response
                    clips = []
                    for item in result.get("videos", []):
                        clips.append({
                            "id": str(item.get("videoId", len(clips))),
                            "title": item.get("title", f"Clip {len(clips)+1}"),
                            "score": float(item.get("viralScore", 8.0)) * 10,  # convert 9.8 → 98
                            "duration": int(item.get("videoMsDuration", 45000)) // 1000,  # ms → seconds
                            "download_url": item.get("videoUrl", ""),
                            "stream_url": item.get("videoUrl", ""),
                            "thumbnail": item.get("coverUrl", ""),
                            "viral_reason": item.get("viralReason", ""),
                        })
                    print(f"Done! {len(clips)} clips")
                    jobs[job_id] = {"status": "done", "progress": 100, "clips": clips}
                    return

                elif code == 2001:
                    jobs[job_id] = {"status": "cutting", "progress": min(40 + attempt, 92)}
                else:
                    jobs[job_id] = {"status": "failed", "error": f"Code {code}: {result.get('errMsg', str(result)[:200])}"}
                    return
            except Exception as e:
                print(f"Poll error: {e}")
                continue

        jobs[job_id] = {"status": "failed", "error": "Timed out"}

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        jobs[job_id] = {"status": "failed", "error": str(e)}

@app.get("/status/{job_id}")
def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job
