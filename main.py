import os, uuid, asyncio, httpx, shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
        res = await client.get(
            VIZARD_QUERY.format(1),
            headers={"VIZARDAI_API_KEY": VIZARD_API_KEY}
        )
    return {"status_code": res.status_code, "response": res.text[:300]}

@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    subtitle_language: str = Form("English"),
    style: str = Form("")
):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "uploading", "progress": 10}

    # Save file locally
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "mp4"
    fname = f"{job_id}.{ext}"
    fpath = UPLOAD_DIR / fname
    with open(fpath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    print(f"File saved: {fname}, size: {fpath.stat().st_size}")

    video_url = f"{BASE_URL}/files/{fname}"
    lang_code = LANG_MAP.get(subtitle_language, "en")

    print(f"Starting Vizard job: {job_id}, url: {video_url}, lang: {lang_code}")
    asyncio.create_task(process_with_vizard(job_id, video_url, ext, lang_code))

    return {"job_id": job_id}

async def process_with_vizard(job_id: str, video_url: str, ext: str, lang: str):
    try:
        jobs[job_id] = {"status": "transcribing", "progress": 25}

        headers = {
            "Content-Type": "application/json",
            "VIZARDAI_API_KEY": VIZARD_API_KEY
        }

        payload = {
            "videoUrl": video_url,
            "videoType": 1,
            "ext": ext,
            "lang": lang,
            "preferLength": [1, 2],
            "ratioOfClip": 1,
            "subtitleSwitch": 1,
            "headlineSwitch": 1,
            "maxClipNumber": 10,
        }

        print(f"Calling Vizard create: {payload}")

        async with httpx.AsyncClient(timeout=120) as client:
            res = await client.post(VIZARD_CREATE, headers=headers, json=payload)

        print(f"Vizard create status: {res.status_code}, body: {res.text[:500]}")

        if res.status_code != 200:
            jobs[job_id] = {"status": "failed", "error": f"Vizard HTTP {res.status_code}: {res.text[:200]}"}
            return

        data = res.json()
        print(f"Vizard create parsed: {data}")

        if data.get("code") != 2000:
            jobs[job_id] = {"status": "failed", "error": f"Vizard code {data.get('code')}: {data.get('message') or data.get('errMsg') or str(data)}"}
            return

        project_id = data["projectId"]
        print(f"Vizard project created: {project_id}")
        jobs[job_id] = {"status": "scoring", "progress": 40, "project_id": project_id}

        # Poll for results — Vizard takes 2-10 minutes
        for attempt in range(150):
            await asyncio.sleep(6)
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    poll = await client.get(
                        VIZARD_QUERY.format(project_id),
                        headers={"VIZARDAI_API_KEY": VIZARD_API_KEY}
                    )
                result = poll.json()
                code = result.get("code")
                print(f"Poll {attempt}: code={code}")

                if code == 2000:
                    # Success — parse clips
                    raw = result.get("data", {})
                    video_list = raw.get("videos") or raw.get("clips") or []
                    clips = []
                    for item in video_list:
                        clips.append({
                            "id": str(item.get("id", len(clips))),
                            "title": item.get("headline") or item.get("title") or item.get("name") or f"Clip {len(clips)+1}",
                            "score": item.get("viralScore") or item.get("viral_score") or 82,
                            "duration": item.get("duration") or 45,
                            "download_url": item.get("videoUrl") or item.get("video_url") or item.get("url") or "",
                            "stream_url": item.get("videoUrl") or item.get("video_url") or item.get("url") or "",
                            "thumbnail": item.get("coverUrl") or item.get("cover_url") or "",
                        })
                    print(f"Done! {len(clips)} clips generated")
                    jobs[job_id] = {"status": "done", "progress": 100, "clips": clips}
                    return

                elif code == 2001:
                    # Still processing
                    progress = min(40 + attempt // 2, 92)
                    jobs[job_id] = {"status": "cutting", "progress": progress}
                    continue

                else:
                    err = result.get("message") or result.get("errMsg") or str(result)[:300]
                    print(f"Vizard poll error: code={code}, msg={err}")
                    jobs[job_id] = {"status": "failed", "error": f"Vizard error {code}: {err}"}
                    return

            except Exception as poll_err:
                print(f"Poll error: {poll_err}")
                continue

        jobs[job_id] = {"status": "failed", "error": "Timed out — video may be too long"}

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"Exception in process_with_vizard: {err}")
        jobs[job_id] = {"status": "failed", "error": str(e)}

@app.get("/status/{job_id}")
def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job

@app.get("/debug-project/{project_id}")
async def debug_project(project_id: str):
    """Debug - query a specific Vizard project"""
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(
            VIZARD_QUERY.format(project_id),
            headers={"VIZARDAI_API_KEY": VIZARD_API_KEY}
        )
    return {"status_code": res.status_code, "raw": res.json()}
