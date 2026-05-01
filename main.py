import os, uuid, shutil, hashlib, hmac, time, json
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import httpx

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("uploads", exist_ok=True)
app.mount("/files", StaticFiles(directory="uploads"), name="files")

VIZARD_API_KEY = os.getenv("VIZARD_API_KEY", "76f3b8d194804562a7fb22584dbd2361")
VIZARD_CREATE  = "https://elb.vizard.ai/gwapi/v1/video/createProject"
VIZARD_QUERY   = "https://elb.vizard.ai/gwapi/v1/video/queryProjectVideo"
BASE_URL       = os.getenv("BASE_URL", "https://web-production-189e9.up.railway.app")

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD", "de5jdqth5")
CLOUD_KEY  = os.getenv("CLOUDINARY_KEY",   "725389195426886")
CLOUD_SEC  = os.getenv("CLOUDINARY_SECRET", "")

jobs = {}

def cloudinary_upload_http(file_path: str, public_id: str) -> str:
    """Upload to Cloudinary using raw HTTP (avoids SDK DNS issues)"""
    timestamp = str(int(time.time()))
    
    # Build signature
    sig_str = f"public_id={public_id}&timestamp={timestamp}{CLOUD_SEC}"
    signature = hashlib.sha1(sig_str.encode()).hexdigest()
    
    upload_url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/video/upload"
    
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, "video/mp4")}
        data = {
            "public_id": public_id,
            "timestamp": timestamp,
            "api_key": CLOUD_KEY,
            "signature": signature,
        }
        r = httpx.post(upload_url, data=data, files=files, timeout=120)
    
    result = r.json()
    print(f"Cloudinary HTTP upload: {r.status_code} | {str(result)[:200]}")
    
    if r.status_code == 200 and "secure_url" in result:
        return result["secure_url"]
    raise Exception(f"Cloudinary upload failed: {result}")


@app.get("/health")
def health():
    return {"status": "ok", "engine": "vizard", "cloudinary": bool(CLOUD_SEC), "base_url": BASE_URL}


@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    local_path = f"uploads/{job_id}{ext}"

    with open(local_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    size = os.path.getsize(local_path)
    print(f"Saved: {job_id}{ext} ({size} bytes)")

    # Try Cloudinary first, fall back to Railway static URL
    video_url = None
    try:
        video_url = cloudinary_upload_http(local_path, f"clipgen_{job_id}")
        print(f"Cloudinary URL: {video_url}")
    except Exception as e:
        print(f"Cloudinary failed: {e} — using Railway URL")
        video_url = f"{BASE_URL}/files/{job_id}{ext}"
        print(f"Railway URL: {video_url}")

    # Create Vizard project
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
        print(f"Vizard create: {data}")
        code = data.get("code")
        project_id = data.get("projectId") or (data.get("data") or {}).get("projectId")

        if code == 2000 and project_id:
            jobs[job_id] = {"status": "processing", "project_id": str(project_id), "clips": []}
        else:
            jobs[job_id] = {"status": "failed", "clips": [], "error": f"Vizard code {code}"}
    except Exception as e:
        print(f"Vizard error: {e}")
        jobs[job_id] = {"status": "failed", "clips": [], "error": str(e)}

    return {"job_id": job_id}


def extract_clips(data: dict) -> list:
    videos = data.get("videos") or (data.get("data") or {}).get("videos") or []
    clips = []
    for i, item in enumerate(videos):
        url   = item.get("videoUrl") or item.get("url") or ""
        title = item.get("title") or item.get("videoTitle") or f"Clip {i+1}"
        try:
            score = int(float(str(item.get("viralScore", 7))) * 10)
        except:
            score = 75
        try:
            dur = int(item.get("videoMsDuration", 30000)) // 1000
        except:
            dur = 30
        reason = item.get("viralReason") or "High viral potential"
        thumb  = item.get("coverUrl") or item.get("thumbnail") or ""
        if url:
            clips.append({"id": i+1, "title": title, "score": score,
                          "duration": dur, "url": url, "thumbnail": thumb, "reason": reason})
    return clips


@app.get("/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found", "clips": []}
    if job["status"] != "processing":
        return job

    project_id = job.get("project_id")
    if not project_id:
        return job

    headers = {"Authorization": f"Bearer {VIZARD_API_KEY}", "Content-Type": "application/json"}
    try:
        r = httpx.post(VIZARD_QUERY, json={"projectId": int(project_id)}, headers=headers, timeout=30)
        data = r.json()
        code = data.get("code")
        print(f"Poll {project_id}: code={code}")

        if code == 2000:
            clips = extract_clips(data)
            if clips:
                print(f"Done! {len(clips)} clips")
                job["status"] = "done"
                job["clips"] = clips
                return job
            # code 2000 but no clips yet = still encoding
            return {"status": "processing", "project_id": project_id, "clips": []}
        elif code == 1000:
            # Normal "still processing" response from Vizard
            return {"status": "processing", "project_id": project_id, "clips": []}
        else:
            print(f"Vizard poll unexpected code {code}: {data}")
            return {"status": "processing", "project_id": project_id, "clips": []}
    except Exception as e:
        print(f"Poll error: {e}")
        return {"status": "processing", "project_id": project_id, "clips": []}


@app.get("/project/{project_id}")
def fetch_project(project_id: str):
    """Fetch clips for any project ID directly"""
    headers = {"Authorization": f"Bearer {VIZARD_API_KEY}", "Content-Type": "application/json"}
    try:
        r = httpx.post(VIZARD_QUERY, json={"projectId": int(project_id)}, headers=headers, timeout=30)
        data = r.json()
        code = data.get("code")
        if code == 2000:
            clips = extract_clips(data)
            return {"status": "done" if clips else "processing", "clips": clips}
        return {"status": "processing", "clips": []}
    except Exception as e:
        return {"status": "error", "clips": [], "error": str(e)}

