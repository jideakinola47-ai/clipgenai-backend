import os, uuid, time, traceback, httpx, shutil
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import cloudinary
import cloudinary.uploader

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("uploads", exist_ok=True)
app.mount("/files", StaticFiles(directory="uploads"), name="files")

VIZARD_API_KEY = os.getenv("VIZARD_API_KEY", "76f3b8d194804562a7fb22584dbd2361")
VIZARD_CREATE = "https://elb.vizard.ai/gwapi/v1/video/createProject"
VIZARD_QUERY  = "https://elb.vizard.ai/gwapi/v1/video/queryProjectVideo"
BASE_URL      = os.getenv("BASE_URL", "https://web-production-189e9.up.railway.app")

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD", "de5jdqth5")
CLOUD_KEY  = os.getenv("CLOUDINARY_KEY", "725389195426886")
CLOUD_SEC  = os.getenv("CLOUDINARY_SECRET", "")

cloudinary.config(cloud_name=CLOUD_NAME, api_key=CLOUD_KEY, api_secret=CLOUD_SEC)

jobs = {}

@app.get("/health")
def health():
    return {"status": "ok", "engine": "vizard", "cloudinary": bool(CLOUD_SEC)}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    local_path = f"uploads/{job_id}{ext}"

    with open(local_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    size = os.path.getsize(local_path)
    print(f"Saved: {job_id}{ext} ({size} bytes)")

    # Upload to Cloudinary
    print("Uploading to Cloudinary...")
    try:
        result = cloudinary.uploader.upload(
            local_path,
            resource_type="video",
            public_id=f"clipgen_{job_id}",
            overwrite=True
        )
        video_url = result["secure_url"]
        print(f"Cloudinary URL: {video_url}")
    except Exception as e:
        print(f"Cloudinary error: {e}")
        # Fallback to Railway URL
        video_url = f"{BASE_URL}/files/{job_id}{ext}"
        print(f"Falling back to Railway URL: {video_url}")

    # Create Vizard project
    print(f"Calling Vizard: {video_url}")
    headers = {"Authorization": f"Bearer {VIZARD_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "videoUrl": video_url,
        "preferLength": "[30,60]",
        "lang": "en",
        "videoType": 1
    }

    try:
        r = httpx.post(VIZARD_CREATE, json=payload, headers=headers, timeout=60)
        data = r.json()
        print(f"Vizard create response: {data}")
    except Exception as e:
        print(f"Vizard create error: {e}")
        jobs[job_id] = {"status": "failed", "clips": [], "error": str(e)}
        return {"job_id": job_id}

    code = data.get("code")
    project_id = data.get("projectId") or data.get("data", {}).get("projectId")

    if code == 2000 and project_id:
        jobs[job_id] = {"status": "processing", "project_id": str(project_id), "clips": []}
        print(f"Project ID: {project_id}")
    else:
        jobs[job_id] = {"status": "failed", "clips": [], "error": f"Vizard code {code}: {data}"}

    return {"job_id": job_id}


def extract_clips(data):
    """Try all known Vizard response structures"""
    clips = []

    # Structure 1: top-level videos array
    videos = data.get("videos") or data.get("data", {}).get("videos", [])

    if not videos:
        return clips

    for i, item in enumerate(videos):
        url   = item.get("videoUrl") or item.get("url") or item.get("downloadUrl", "")
        title = item.get("title") or item.get("videoTitle") or f"Clip {i+1}"
        score_raw = item.get("viralScore") or item.get("viral_score") or 0
        try:
            score = int(float(str(score_raw)) * 10)
        except:
            score = 75

        dur_ms = item.get("videoMsDuration") or item.get("duration") or 30000
        try:
            dur_s = int(dur_ms) // 1000
        except:
            dur_s = 30

        reason = item.get("viralReason") or item.get("reason") or "High engagement potential"
        thumb  = item.get("coverUrl") or item.get("thumbnail") or ""

        if url:
            clips.append({
                "id": i + 1,
                "title": title,
                "score": score,
                "duration": dur_s,
                "url": url,
                "thumbnail": thumb,
                "reason": reason
            })

    return clips


@app.get("/status/{job_id}")
def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found", "clips": []}

    if job["status"] != "processing":
        return job

    project_id = job.get("project_id")
    if not project_id:
        return job

    # Poll Vizard - code 1000 just means still processing, keep waiting
    headers = {"Authorization": f"Bearer {VIZARD_API_KEY}", "Content-Type": "application/json"}
    try:
        r = httpx.post(VIZARD_QUERY, json={"projectId": int(project_id)}, headers=headers, timeout=30)
        data = r.json()
        code = data.get("code")
        print(f"Poll project {project_id}: code={code}")

        if code == 2000:
            clips = extract_clips(data)
            if clips:
                print(f"Got {len(clips)} clips!")
                job["status"] = "done"
                job["clips"] = clips
                return job
            else:
                # 2000 but no clips yet — still processing
                print(f"Code 2000 but no clips yet, still processing...")
                return {"status": "processing", "project_id": project_id, "clips": []}

        elif code == 1000:
            # 1000 = still processing, totally normal — just wait
            print(f"Code 1000 = still processing, waiting...")
            return {"status": "processing", "project_id": project_id, "clips": []}

        elif code == 4002:
            # Project not found
            job["status"] = "failed"
            job["error"] = "Project not found on Vizard"
            return job

        else:
            print(f"Unknown code {code}: {data}")
            return {"status": "processing", "project_id": project_id, "clips": []}

    except Exception as e:
        print(f"Poll error: {e}")
        return {"status": "processing", "project_id": project_id, "clips": []}


@app.get("/project/{project_id}")
def get_project(project_id: str):
    """Fetch clips directly for any project ID"""
    headers = {"Authorization": f"Bearer {VIZARD_API_KEY}", "Content-Type": "application/json"}
    try:
        r = httpx.post(VIZARD_QUERY, json={"projectId": int(project_id)}, headers=headers, timeout=30)
        data = r.json()
        code = data.get("code")
        print(f"Direct project fetch {project_id}: code={code}, data={str(data)[:300]}")

        if code == 2000:
            clips = extract_clips(data)
            return {"status": "done" if clips else "processing", "clips": clips}
        elif code == 1000:
            return {"status": "processing", "clips": []}
        else:
            return {"status": "failed", "clips": [], "error": f"code {code}"}
    except Exception as e:
        return {"status": "error", "clips": [], "error": str(e)}

