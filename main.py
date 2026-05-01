import os, uuid, shutil
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import httpx

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("uploads", exist_ok=True)
app.mount("/files", StaticFiles(directory="uploads"), name="files")

VIZARD_KEY    = os.getenv("VIZARD_API_KEY", "76f3b8d194804562a7fb22584dbd2361")
VIZARD_CREATE = "https://elb.vizard.ai/gwapi/v1/video/createProject"
VIZARD_QUERY  = "https://elb.vizard.ai/gwapi/v1/video/queryProjectVideo"

jobs = {}

@app.get("/health")
def health():
    return {"status": "ok", "engine": "vizard"}

@app.post("/start")
async def start(payload: dict):
    """Receive a public video URL (from Cloudinary), send to Vizard"""
    video_url = payload.get("url")
    job_id    = payload.get("job_id", str(uuid.uuid4()))
    if not video_url:
        return {"error": "no url"}

    print(f"Starting job {job_id} with URL: {video_url}")
    headers = {"Authorization": f"Bearer {VIZARD_KEY}", "Content-Type": "application/json"}
    try:
        r = httpx.post(VIZARD_CREATE, json={
            "videoUrl": video_url,
            "preferLength": "[30,60]",
            "lang": "en",
            "videoType": 1
        }, headers=headers, timeout=60)
        data = r.json()
        print(f"Vizard create: {data}")
        code       = data.get("code")
        project_id = data.get("projectId") or (data.get("data") or {}).get("projectId")

        if code == 2000 and project_id:
            jobs[job_id] = {"status": "processing", "project_id": str(project_id), "clips": []}
            return {"job_id": job_id, "project_id": project_id}
        else:
            jobs[job_id] = {"status": "failed", "error": str(data), "clips": []}
            return {"job_id": job_id, "error": str(data)}
    except Exception as e:
        print(f"Vizard error: {e}")
        jobs[job_id] = {"status": "failed", "error": str(e), "clips": []}
        return {"job_id": job_id, "error": str(e)}

@app.get("/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found", "clips": []}
    if job["status"] != "processing":
        return job

    project_id = job.get("project_id")
    headers = {"Authorization": f"Bearer {VIZARD_KEY}", "Content-Type": "application/json"}
    try:
        r = httpx.post(VIZARD_QUERY, json={"projectId": int(project_id)}, headers=headers, timeout=30)
        data = r.json()
        code = data.get("code")
        print(f"Poll {project_id}: code={code}")

        if code == 2000:
            videos = data.get("videos") or []
            if videos:
                clips = []
                for i, v in enumerate(videos):
                    try: score = int(float(str(v.get("viralScore", 7))) * 10)
                    except: score = 75
                    try: dur = int(v.get("videoMsDuration", 30000)) // 1000
                    except: dur = 30
                    clips.append({
                        "id": i+1,
                        "title": v.get("title") or v.get("videoTitle") or f"Clip {i+1}",
                        "score": score,
                        "duration": dur,
                        "url": v.get("videoUrl") or v.get("url") or "",
                        "thumbnail": v.get("coverUrl") or "",
                        "reason": v.get("viralReason") or "High viral potential"
                    })
                print(f"Done! {len(clips)} clips")
                job["status"] = "done"
                job["clips"] = clips
                return job
        # code 1000 or 2000 with no videos = still processing
        return {"status": "processing", "project_id": project_id, "clips": []}
    except Exception as e:
        print(f"Poll error: {e}")
        return {"status": "processing", "project_id": project_id, "clips": []}

