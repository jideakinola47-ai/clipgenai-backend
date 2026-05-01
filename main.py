import os, uuid, shutil, json, requests as req
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("uploads", exist_ok=True)
os.makedirs("jobs", exist_ok=True)
app.mount("/files", StaticFiles(directory="uploads"), name="files")

VIZARD_KEY    = os.getenv("VIZARD_API_KEY", "76f3b8d194804562a7fb22584dbd2361")
VIZARD_CREATE = "https://elb.vizard.ai/gwapi/v1/video/createProject"
VIZARD_QUERY  = "https://elb.vizard.ai/gwapi/v1/video/queryProjectVideo"
BASE_URL      = os.getenv("BASE_URL", "https://web-production-189e9.up.railway.app")

def save_job(job_id, data):
    with open(f"jobs/{job_id}.json", "w") as f:
        json.dump(data, f)

def load_job(job_id):
    try:
        with open(f"jobs/{job_id}.json") as f:
            return json.load(f)
    except:
        return None

@app.get("/health")
def health():
    try:
        r = req.get("https://elb.vizard.ai", timeout=5)
        vizard_ok = True
    except Exception as e:
        vizard_ok = str(e)
    return {"status": "ok", "vizard_reachable": vizard_ok, "base_url": BASE_URL}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    ext    = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    path   = f"uploads/{job_id}{ext}"

    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    size      = os.path.getsize(path)
    video_url = f"{BASE_URL}/files/{job_id}{ext}"
    print(f"Saved {size} bytes → {video_url}")

    headers = {"Authorization": f"Bearer {VIZARD_KEY}", "Content-Type": "application/json"}
    payload = {"videoUrl": video_url, "preferLength": "[30,60]", "lang": "en", "videoType": 1}

    try:
        r    = req.post(VIZARD_CREATE, json=payload, headers=headers, timeout=60)
        data = r.json()
        print(f"Vizard create: {data}")
        pid  = data.get("projectId")

        if data.get("code") == 2000 and pid:
            save_job(job_id, {"status": "processing", "project_id": str(pid), "clips": []})
        else:
            save_job(job_id, {"status": "failed", "error": str(data), "clips": []})
    except Exception as e:
        print(f"Vizard error: {e}")
        save_job(job_id, {"status": "failed", "error": str(e), "clips": []})

    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = load_job(job_id)
    if not job:
        return {"status": "not_found", "clips": []}
    if job["status"] != "processing":
        return job

    pid     = job.get("project_id")
    headers = {"Authorization": f"Bearer {VIZARD_KEY}", "Content-Type": "application/json"}
    try:
        r    = req.post(VIZARD_QUERY, json={"projectId": int(pid)}, headers=headers, timeout=30)
        data = r.json()
        code = data.get("code")
        print(f"Poll {pid}: code={code}, keys={list(data.keys())}")
        print(f"Full response: {str(data)[:500]}")

        if code == 2000:
            videos = data.get("videos") or []
            print(f"Videos count: {len(videos)}")
            if videos:
                clips = []
                for i, v in enumerate(videos):
                    print(f"Video {i}: {str(v)[:200]}")
                    try:    score = int(float(str(v.get("viralScore", 7))) * 10)
                    except: score = 75
                    try:    dur = int(v.get("videoMsDuration", 30000)) // 1000
                    except: dur = 30
                    clips.append({
                        "id": i+1,
                        "title": v.get("title") or v.get("videoTitle") or f"Clip {i+1}",
                        "score": score, "duration": dur,
                        "url": v.get("videoUrl") or v.get("url") or "",
                        "thumbnail": v.get("coverUrl") or "",
                        "reason": v.get("viralReason") or "High viral potential"
                    })
                job.update({"status": "done", "clips": clips})
                save_job(job_id, job)
                print(f"Done! {len(clips)} clips saved")
                return job

        # Still processing
        return {"status": "processing", "project_id": pid, "clips": []}
    except Exception as e:
        print(f"Poll error: {e}")
        return {"status": "processing", "project_id": pid, "clips": []}
