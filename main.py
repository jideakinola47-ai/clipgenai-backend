from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import openai
import os
import uuid
import json
import asyncio
import tempfile
import subprocess
from pathlib import Path

app = FastAPI(title="ClipGen.AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = openai.OpenAI(api_key=OPENAI_API_KEY)

jobs = {}
UPLOAD_DIR = Path("/tmp/clipgenai")
UPLOAD_DIR.mkdir(exist_ok=True)

@app.get("/")
def root():
    return {"status": "ClipGen.AI backend running", "version": "1.0"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    style: str = Form("motivational"),
    platforms: str = Form("TikTok,Instagram Reels,YouTube Shorts"),
    subtitle_language: str = Form("English")
):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "uploading", "step": 0, "clips": [], "error": None}

    # Save uploaded file
    video_path = UPLOAD_DIR / f"{job_id}_{file.filename}"
    with open(video_path, "wb") as f:
        content = await file.read()
        f.write(content)

    jobs[job_id]["status"] = "transcribing"
    jobs[job_id]["step"] = 1

    # Run pipeline in background
    asyncio.create_task(process_video(job_id, video_path, style, subtitle_language))

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

async def process_video(job_id: str, video_path: Path, style: str, language: str):
    try:
        # Step 1: Transcribe with Whisper
        jobs[job_id] = {**jobs[job_id], "status": "transcribing", "step": 1}
        
        with open(video_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"]
            )

        # Step 2: AI Scoring with GPT-4
        jobs[job_id] = {**jobs[job_id], "status": "scoring", "step": 2}

        segments_text = "\n".join([
            f"[{s.start:.1f}s - {s.end:.1f}s]: {s.text}"
            for s in transcript.segments
        ])

        prompt = f"""You are a viral content expert. Analyze this video transcript and find the 5 best segments for viral short clips.

Style requested: {style}

Transcript with timestamps:
{segments_text}

Return ONLY valid JSON array with exactly 5 clips:
[
  {{
    "start": 12.5,
    "end": 40.2,
    "hook_title": "DISCIPLINE TODAY",
    "hook_subtitle": "SUCCESS TOMORROW",
    "viral_score": 94,
    "viral_level": "Very High",
    "reason": "Strong opening hook with emotional appeal"
  }}
]

Rules:
- Each clip must be 15-60 seconds long
- Score 60-99 based on: hook strength, emotion, pacing, value
- hook_title max 4 words, ALL CAPS
- hook_subtitle max 4 words, ALL CAPS
- Style adaptation: {style}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )

        clips_data = json.loads(response.choices[0].message.content.strip())

        # Step 3: Cut clips with FFmpeg
        jobs[job_id] = {**jobs[job_id], "status": "cutting", "step": 3}

        output_dir = UPLOAD_DIR / job_id
        output_dir.mkdir(exist_ok=True)

        clips = []
        for i, clip in enumerate(clips_data[:5]):
            output_file = output_dir / f"clip_{i+1}.mp4"
            duration = clip["end"] - clip["start"]

            # FFmpeg command: cut + 9:16 format + subtitles
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(clip["start"]),
                "-i", str(video_path),
                "-t", str(duration),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
                "-c:v", "libx264",
                "-c:a", "aac",
                "-preset", "fast",
                str(output_file)
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=120)

            if result.returncode == 0:
                clips.append({
                    "id": i + 1,
                    "filename": f"clip_{i+1}.mp4",
                    "download_url": f"/download/{job_id}/clip_{i+1}.mp4",
                    "hook_title": clip["hook_title"],
                    "hook_subtitle": clip["hook_subtitle"],
                    "viral_score": clip["viral_score"],
                    "viral_level": clip["viral_level"],
                    "reason": clip.get("reason", ""),
                    "duration": f"{int(duration//60)}:{int(duration%60):02d}",
                    "color": get_score_color(clip["viral_score"])
                })

        jobs[job_id] = {
            "status": "done",
            "step": 5,
            "clips": clips,
            "error": None
        }

        # Cleanup original video
        video_path.unlink(missing_ok=True)

    except Exception as e:
        jobs[job_id] = {
            **jobs[job_id],
            "status": "failed",
            "error": str(e),
            "clips": []
        }

def get_score_color(score):
    if score >= 90: return "#34d399"
    if score >= 80: return "#a78bfa"
    if score >= 70: return "#fbbf24"
    return "#fb923c"
