import os, uuid, json, subprocess, asyncio, tempfile, re, shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import openai

app = FastAPI()

# Allow EVERY origin - no restrictions
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Override any host checking
@app.middleware("http")
async def allow_all_hosts(request: Request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response

JOBS = {}
CLIPS_DIR = Path("/tmp/clips")
CLIPS_DIR.mkdir(exist_ok=True)

def get_ai_client():
    return openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

def get_duration(path):
    r = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=30
    )
    try: return float(r.stdout.strip())
    except: return 300.0

def extract_audio_fast(video_path, audio_path):
    """Extract mono 16kHz audio - smallest possible for Whisper"""
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ar", "16000", "-ac", "1",
        "-b:a", "24k", "-f", "mp3",
        str(audio_path)
    ], capture_output=True, timeout=300)

def cut_clip_fast(video_path, start, end, out_path):
    """Cut clip fast with ultrafast preset"""
    dur = max(float(end) - float(start), 5)
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(float(start)),
        "-i", str(video_path),
        "-t", str(dur),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart",
        str(out_path)
    ], capture_output=True, timeout=120)

async def process_video(job_id: str, video_path: str, style: str, lang: str):
    try:
        JOBS[job_id] = {"status": "transcribing", "progress": 15, "clips": [], "error": None}

        duration = await asyncio.get_event_loop().run_in_executor(None, get_duration, video_path)

        # Extract tiny audio file
        audio_path = video_path + "_audio.mp3"
        await asyncio.get_event_loop().run_in_executor(None, extract_audio_fast, video_path, audio_path)

        JOBS[job_id]["progress"] = 35

        # Transcribe
        ai = get_ai_client()
        with open(audio_path, "rb") as f:
            transcript = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ai.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"]
                )
            )

        # Cleanup audio immediately
        try: os.remove(audio_path)
        except: pass

        JOBS[job_id]["progress"] = 55
        JOBS[job_id]["status"] = "scoring"

        # Build segments text
        segs = getattr(transcript, "segments", []) or []
        seg_lines = "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in segs[:60])
        if not seg_lines:
            seg_lines = (getattr(transcript, "text", "") or "")[:2000]

        # GPT analysis
        prompt = f"""Video duration: {duration:.0f}s. Style: {style}.
Transcript:
{seg_lines[:2500]}

Find 5 best viral moments (30-75 seconds each). Return ONLY this JSON array, nothing else:
[{{"start":10.5,"end":65.0,"title":"CATCHY TITLE CAPS","score":92}},{{"start":80.0,"end":140.0,"title":"SECOND TITLE","score":85}},{{"start":150.0,"end":210.0,"title":"THIRD TITLE","score":79}},{{"start":220.0,"end":280.0,"title":"FOURTH TITLE","score":73}},{{"start":290.0,"end":340.0,"title":"FIFTH TITLE","score":67}}]

Rules: start/end within 0-{duration:.0f}, each clip 30-75s, score 0-100, titles ALL CAPS short."""

        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.5
            )
        )

        JOBS[job_id]["progress"] = 70
        JOBS[job_id]["status"] = "cutting"

        # Parse moments
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'```[a-z]*|```', '', raw).strip()
        try:
            moments = json.loads(raw)
            if not isinstance(moments, list): raise ValueError()
        except:
            # Reliable fallback
            step = duration / 6
            moments = [
                {"start": step*1, "end": step*1+60, "title": "KEY MOMENT", "score": 91},
                {"start": step*2, "end": step*2+60, "title": "TOP INSIGHT", "score": 84},
                {"start": step*3, "end": step*3+55, "title": "MUST WATCH", "score": 78},
                {"start": step*4, "end": step*4+50, "title": "VIRAL CLIP", "score": 72},
                {"start": step*4+60, "end": step*4+110, "title": "BEST PART", "score": 66},
            ]

        # Cut clips
        job_dir = CLIPS_DIR / job_id
        job_dir.mkdir(exist_ok=True)
        clips = []

        for i, m in enumerate(moments[:5]):
            try:
                s = max(0.0, float(m.get("start", 0)))
                e = float(m.get("end", s + 60))
                s = min(s, duration - 10)
                e = min(e, duration)
                if e - s < 5: e = min(s + 60, duration)

                out = job_dir / f"clip_{i+1}.mp4"
                await asyncio.get_event_loop().run_in_executor(None, cut_clip_fast, video_path, s, e, str(out))

                if out.exists() and out.stat().st_size > 5000:
                    clips.append({
                        "id": f"clip_{i+1}",
                        "title": str(m.get("title", f"CLIP {i+1}")),
                        "score": int(m.get("score", 75)),
                        "duration": round(e - s, 1),
                        "download_url": f"/download/{job_id}/clip_{i+1}.mp4",
                        "stream_url": f"/stream/{job_id}/clip_{i+1}.mp4",
                    })
            except Exception as clip_err:
                print(f"Clip {i+1} failed: {clip_err}")

            JOBS[job_id]["progress"] = 70 + (i + 1) * 6

        if not clips:
            raise Exception("Could not generate clips. Please try a different video file.")

        JOBS[job_id].update({"status": "done", "progress": 100, "clips": clips})

    except Exception as e:
        JOBS[job_id] = {"status": "failed", "progress": 0, "clips": [], "error": str(e)}
    finally:
        try: os.remove(video_path)
        except: pass


@app.get("/")
@app.get("/health")
def health():
    return {"status": "ClipGen.AI backend running", "version": "3.0", "ok": True}

@app.options("/{rest:path}")
async def preflight(rest: str):
    return JSONResponse({}, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })

@app.post("/upload")
async def upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    style: str = Form("General"),
    subtitle_language: str = Form("English")
):
    job_id = str(uuid.uuid4())
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    tmp = tempfile.mktemp(suffix=suffix, dir="/tmp")

    with open(tmp, "wb") as f:
        content = await file.read()
        f.write(content)

    JOBS[job_id] = {"status": "uploading", "progress": 5, "clips": [], "error": None}
    background_tasks.add_task(process_video, job_id, tmp, style, subtitle_language)
    return {"job_id": job_id, "status": "started"}

@app.get("/status/{job_id}")
def get_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Job not found")
    return JOBS[job_id]

@app.get("/download/{job_id}/{filename}")
def download_clip(job_id: str, filename: str):
    path = CLIPS_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(404, "Clip not found")
    return FileResponse(str(path), media_type="video/mp4",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.get("/stream/{job_id}/{filename}")
def stream_clip(job_id: str, filename: str):
    path = CLIPS_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(404, "Clip not found")
    return FileResponse(str(path), media_type="video/mp4")
