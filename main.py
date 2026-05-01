import os, uuid, asyncio, httpx, shutil, base64
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

VIZARD_API_KEY    = os.getenv("VIZARD_API_KEY", "76f3b8d194804562a7fb22584dbd2361")
CLOUDINARY_CLOUD  = os.getenv("CLOUDINARY_CLOUD", "")
CLOUDINARY_KEY    = os.getenv("CLOUDINARY_KEY", "")
CLOUDINARY_SECRET = os.getenv("CLOUDINARY_SECRET", "")
VIZARD_CREATE     = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/create"
VIZARD_QUERY      = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/query/{}"

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
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

def parse_clips(result):
    clips = []
    for item in result.get("videos", []):
        url = item.get("videoUrl", "")
        try:
            score = round(float(item.get("viralScore", "8.0")) * 10)
        except:
            score = 80
        try:
            duration = int(item.get("videoMsDuration", 45000)) // 1000
        except:
            duration = 45
        clips.append({
            "id": str(item.get("videoId", len(clips))),
            "title": item.get("title", f"Clip {len(clips)+1}"),
            "score": score,
            "duration": duration,
            "download_url": url,
            "stream_url": url,
            "thumbnail": item.get("coverUrl", ""),
            "viral_reason": item.get("viralReason", ""),
        })
    return clips

async def upload_to_cloudinary(fpath: Path, ext: str) -> str:
    """Upload file to Cloudinary and return public URL"""
    import hashlib, time, hmac
    timestamp = str(int(time.time()))
    signature_str = f"timestamp={timestamp}{CLOUDINARY_SECRET}"
    signature = hashlib.sha1(signature_str.encode()).hexdigest()
    
    with open(fpath, "rb") as f:
        file_data = f.read()
    
    async with httpx.AsyncClient(timeout=300) as client:
        res = await client.post(
            f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD}/video/upload",
            data={
                "api_key": CLOUDINARY_KEY,
                "timestamp": timestamp,
                "signature": signature,
                "resource_type": "video",
            },
            files={"file": (f"video.{ext}", file_data, f"video/{ext}")}
        )
    
    data = res.json()
    print(f"Cloudinary upload: {res.status_code} | {str(data)[:300]}")
    return data.get("secure_url", "")

@app.get("/health")
def health():
    return {"status": "ok", "engine": "vizard", "cloudinary": bool(CLOUDINARY_CLOUD)}

@app.get("/project/{project_id}")
async def get_project(project_id: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(VIZARD_QUERY.format(project_id), headers={"VIZARDAI_API_KEY": VIZARD_API_KEY})
        result = res.json()
        code = result.get("code")
        if code == 2000:
            return {"status": "done", "progress": 100, "clips": parse_clips(result)}
        elif code == 2001:
            return {"status": "cutting", "progress": 60, "clips": []}
        else:
            return {"status": "failed", "error": str(result), "clips": []}
    except Exception as e:
        return {"status": "failed", "error": str(e), "clips": []}

@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    subtitle_language: str = Form("English"),
    style: str = Form("")
):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "uploading", "progress": 10}

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "mp4"
    fname = f"{job_id}.{ext}"
    fpath = UPLOAD_DIR / fname
    with open(fpath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    print(f"Saved: {fname} ({fpath.stat().st_size} bytes)")
    lang = LANG_MAP.get(subtitle_language, "en")
    asyncio.create_task(process_vizard(job_id, fpath, ext, lang))
    return {"job_id": job_id}

async def process_vizard(job_id: str, fpath: Path, ext: str, lang: str):
    try:
        jobs[job_id] = {"status": "transcribing", "progress": 25}

        # Upload to Cloudinary for a reliable public URL
        print(f"Uploading to Cloudinary...")
        video_url = await upload_to_cloudinary(fpath, ext)
        
        if not video_url:
            jobs[job_id] = {"status": "failed", "error": "Failed to upload to Cloudinary"}
            return

        print(f"Cloudinary URL: {video_url}")
        jobs[job_id] = {"status": "scoring", "progress": 40}

        headers = {"Content-Type": "application/json", "VIZARDAI_API_KEY": VIZARD_API_KEY}
        payload = {
            "videoUrl": video_url,
            "videoType": 1,
            "ext": ext,
            "lang": lang,
            "preferLength": "[1,2]",
            "ratioOfClip": 1,
            "subtitleSwitch": 1,
            "headlineSwitch": 1,
            "maxClipNumber": 10,
        }

        print(f"Calling Vizard: {video_url}")
        async with httpx.AsyncClient(timeout=120) as client:
            res = await client.post(VIZARD_CREATE, headers=headers, json=payload)

        print(f"Vizard: {res.status_code} | {res.text[:300]}")

        if res.status_code != 200:
            jobs[job_id] = {"status": "failed", "error": f"HTTP {res.status_code}"}
            return

        data = res.json()
        if data.get("code") != 2000:
            err = data.get("errMsg") or str(data)
            jobs[job_id] = {"status": "failed", "error": f"Code {data.get('code')}: {err}"}
            return

        project_id = str(data.get("projectId"))
        print(f"Project ID: {project_id}")
        jobs[job_id] = {"status": "cutting", "progress": 50, "project_id": project_id}

        for attempt in range(200):
            await asyncio.sleep(6)
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    poll = await client.get(VIZARD_QUERY.format(project_id), headers={"VIZARDAI_API_KEY": VIZARD_API_KEY})
                result = poll.json()
                code = result.get("code")
                print(f"Poll {attempt}: code={code}")

                if code == 2000:
                    clips = parse_clips(result)
                    print(f"Done! {len(clips)} clips")
                    jobs[job_id] = {"status": "done", "progress": 100, "clips": clips}
                    return
                elif code == 2001:
                    jobs[job_id] = {"status": "cutting", "progress": min(50 + attempt // 2, 92), "project_id": project_id}
                else:
                    jobs[job_id] = {"status": "failed", "error": f"Code {code}", "project_id": project_id}
                    return
            except Exception as e:
                print(f"Poll error: {e}")
                continue

        jobs[job_id] = {"status": "failed", "error": "Timed out", "project_id": project_id}

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        jobs[job_id] = {"status": "failed", "error": str(e)}

@app.get("/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
