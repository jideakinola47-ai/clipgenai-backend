import os
import uuid
import json
import asyncio
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import openai

app = FastAPI(title="ClipGen.AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
UPLOAD_DIR = Path("/tmp/clipgenai")
UPLOAD_DIR.mkdir(exist_ok=True)
jobs = {}

@app.get("/")
def root():
    return {"status": "ClipGen.AI backend running", "version": "1.0"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/upload")
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    style: str = Form("motivational"),
    subtitle_language: str = Form("English")
):
    # Check file size - allow up to 500MB
    content = await file.read()
    file_size = len(content)
    
    if file_size > 500 * 1024 * 1024:  # 500MB
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 500MB.")

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "uploading", "step": 0, "clips": [], "error": None}

    video_path = UPLOAD_DIR / f"{job_id}_{file.filename}"
    with open(video_path, "wb") as f:
        f.write(content)

    jobs[job_id]["status"] = "transcribing"
    jobs[job_id]["step"] = 1

    asyncio.create_task(process_video(job_id, video_path, style))
    return {"job_id": job_id, "status": "processing"}

@app.get("/status/{job_id}")
def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]

@app.get("/download/{job_id}/{filename}")
def download_clip(job_id: str, filename: str):
    file_path = UPLOAD_DIR / job_id / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="video/mp4", filename=filename)

async def process_video(job_id: str, video_path: Path, style: str):
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        
        jobs[job_id] = {**jobs[job_id], "status": "transcribing", "step": 1}
        with open(video_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"]
            )

        jobs[job_id] = {**jobs[job_id], "status": "scoring", "step": 2}
        segments_text = "\n".join([
            f"[{s.start:.1f}s - {s.end:.1f}s]: {s.text}"
            for s in transcript.segments
        ])

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""Analyze this transcript and find 5 best viral clips.
Style: {style}
Transcript: {segments_text}

Return ONLY a JSON array with no extra text:
[{{"start":0,"end":30,"hook_title":"TITLE HERE","hook_subtitle":"SUBTITLE HERE","viral_score":90,"viral_level":"Very High"}}]

Rules: clips 15-60 seconds, titles ALL CAPS max 4 words, scores 60-99."""}],
            temperature=0.7
        )

        raw = response.choices[0].message.content.strip()
        # Clean JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        clips_data = json.loads(raw.strip())
        
        jobs[job_id] = {**jobs[job_id], "status": "cutting", "step": 3}

        output_dir = UPLOAD_DIR / job_id
        output_dir.mkdir(exist_ok=True)
        clips = []

        for i, clip in enumerate(clips_data[:5]):
            output_file = output_dir / f"clip_{i+1}.mp4"
            duration = clip["end"] - clip["start"]
            if duration < 5:
                duration = 30
            
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(clip["start"]),
                "-i", str(video_path),
                "-t", str(duration),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
                "-c:v", "libx264", "-c:a", "aac", "-preset", "fast",
                str(output_file)
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=180)
            if result.returncode == 0:
                score = clip.get("viral_score", 80)
                color = "#34d399" if score >= 90 else "#a78bfa" if score >= 80 else "#fbbf24" if score >= 70 else "#fb923c"
                clips.append({
                    "id": i + 1,
                    "filename": f"clip_{i+1}.mp4",
                    "download_url": f"/download/{job_id}/clip_{i+1}.mp4",
                    "hook_title": clip.get("hook_title", "VIRAL CLIP"),
                    "hook_subtitle": clip.get("hook_subtitle", "WATCH NOW"),
                    "viral_score": score,
                    "viral_level": clip.get("viral_level", "High"),
                    "duration": f"{int(duration//60)}:{int(duration%60):02d}",
                    "color": color
                })

        jobs[job_id] = {"status": "done", "step": 5, "clips": clips, "error": None}
        video_path.unlink(missing_ok=True)

    except Exception as e:
        jobs[job_id] = {**jobs[job_id], "status": "failed", "error": str(e), "clips": []}
