import os
import uuid
import json
import asyncio
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
    file: UploadFile = File(...),
    style: str = Form("motivational"),
    subtitle_language: str = Form("English")
):
    content = await file.read()
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Max 500MB.")

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

def get_video_duration(video_path):
    """Get video duration using ffprobe"""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", str(video_path)
        ], capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except:
        return 60.0

async def process_video(job_id: str, video_path: Path, style: str):
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)

        # Step 1: Transcribe
        jobs[job_id] = {**jobs[job_id], "status": "transcribing", "step": 1}
        
        with open(video_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"]
            )

        # Get video duration
        duration_total = get_video_duration(video_path)
        
        # Step 2: AI Scoring
        jobs[job_id] = {**jobs[job_id], "status": "scoring", "step": 2}
        
        segments = getattr(transcript, 'segments', [])
        
        if segments:
            segments_text = "\n".join([
                f"[{s['start']:.1f}s - {s['end']:.1f}s]: {s['text']}"
                if isinstance(s, dict) else
                f"[{s.start:.1f}s - {s.end:.1f}s]: {s.text}"
                for s in segments[:50]  # limit to first 50 segments
            ])
        else:
            # No segments - create clips based on duration
            segments_text = f"Video duration: {duration_total:.0f} seconds. No transcript available."

        prompt = f"""You are a viral content expert. Analyze this video and create 5 viral short clips.

Style: {style}
Video duration: {duration_total:.0f} seconds
Transcript: {segments_text}

Create 5 clips spread throughout the video. Each clip 15-45 seconds long.
Return ONLY valid JSON array, no other text, no markdown:
[{{"start":5.0,"end":35.0,"hook_title":"TITLE HERE","hook_subtitle":"SUBTITLE HERE","viral_score":90,"viral_level":"Very High"}},{{"start":40.0,"end":75.0,"hook_title":"SECOND TITLE","hook_subtitle":"SECOND SUBTITLE","viral_score":85,"viral_level":"High"}},{{"start":80.0,"end":115.0,"hook_title":"THIRD TITLE","hook_subtitle":"THIRD SUBTITLE","viral_score":80,"viral_level":"High"}},{{"start":120.0,"end":150.0,"hook_title":"FOURTH TITLE","hook_subtitle":"FOURTH SUBTITLE","viral_score":75,"viral_level":"Medium"}},{{"start":155.0,"end":185.0,"hook_title":"FIFTH TITLE","hook_subtitle":"FIFTH SUBTITLE","viral_score":70,"viral_level":"Medium"}}]

Rules:
- ALL CAPS for titles, max 4 words each
- start time must be >= 0
- end time must be <= {duration_total:.0f}
- each clip 15-45 seconds
- spread clips throughout the video
- Return ONLY the JSON array"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=800
        )

        raw = response.choices[0].message.content.strip()
        
        # Clean up response - remove markdown if present
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        
        # Find JSON array
        start_idx = raw.find('[')
        end_idx = raw.rfind(']') + 1
        if start_idx >= 0 and end_idx > start_idx:
            raw = raw[start_idx:end_idx]
        
        try:
            clips_data = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: create clips manually based on duration
            clips_data = []
            segment_size = duration_total / 5
            titles = ["KEY INSIGHT", "POWERFUL MOMENT", "VIRAL HOOK", "BEST PART", "TOP HIGHLIGHT"]
            subtitles = ["WATCH THIS", "SHARE NOW", "MUST SEE", "EPIC CLIP", "DON'T MISS"]
            scores = [92, 87, 83, 78, 74]
            levels = ["Very High", "High", "High", "Medium", "Medium"]
            
            for i in range(5):
                start = i * segment_size + 2
                end = start + 30
                if end > duration_total:
                    end = duration_total - 1
                clips_data.append({
                    "start": start, "end": end,
                    "hook_title": titles[i], "hook_subtitle": subtitles[i],
                    "viral_score": scores[i], "viral_level": levels[i]
                })

        # Step 3: Cut clips
        jobs[job_id] = {**jobs[job_id], "status": "cutting", "step": 3}

        output_dir = UPLOAD_DIR / job_id
        output_dir.mkdir(exist_ok=True)
        clips = []

        for i, clip in enumerate(clips_data[:5]):
            try:
                output_file = output_dir / f"clip_{i+1}.mp4"
                start = float(clip.get("start", 0))
                end = float(clip.get("end", start + 30))
                duration = end - start
                
                # Ensure valid duration
                if duration < 5:
                    duration = 25
                if duration > 60:
                    duration = 60
                if start < 0:
                    start = 0
                if start + duration > duration_total:
                    start = max(0, duration_total - duration - 1)

                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start),
                    "-i", str(video_path),
                    "-t", str(duration),
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
                    "-c:v", "libx264", "-c:a", "aac",
                    "-preset", "ultrafast",
                    "-crf", "28",
                    str(output_file)
                ]

                result = subprocess.run(cmd, capture_output=True, timeout=120)
                
                if result.returncode == 0 and output_file.exists():
                    score = int(clip.get("viral_score", 80))
                    color = "#34d399" if score >= 90 else "#a78bfa" if score >= 80 else "#fbbf24" if score >= 70 else "#fb923c"
                    clips.append({
                        "id": i + 1,
                        "filename": f"clip_{i+1}.mp4",
                        "download_url": f"/download/{job_id}/clip_{i+1}.mp4",
                        "hook_title": str(clip.get("hook_title", f"CLIP {i+1}")),
                        "hook_subtitle": str(clip.get("hook_subtitle", "WATCH NOW")),
                        "viral_score": score,
                        "viral_level": str(clip.get("viral_level", "High")),
                        "duration": f"{int(duration//60)}:{int(duration%60):02d}",
                        "color": color
                    })
            except Exception as clip_error:
                print(f"Clip {i+1} failed: {clip_error}")
                continue

        if clips:
            jobs[job_id] = {"status": "done", "step": 5, "clips": clips, "error": None}
        else:
            jobs[job_id] = {"status": "failed", "step": 0, "clips": [], "error": "Could not generate clips. Please try a different video."}

        try:
            video_path.unlink(missing_ok=True)
        except:
            pass

    except Exception as e:
        jobs[job_id] = {**jobs[job_id], "status": "failed", "error": str(e), "clips": []}
