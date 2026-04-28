import os
import uuid
import json
import asyncio
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
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
    return {"status": "ClipGen.AI backend running", "version": "2.0"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/import-url")
async def import_url(url: str = Form(...), style: str = Form("motivational"), subtitle_language: str = Form("English")):
    """Import video from YouTube/TikTok/Instagram URL"""
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "downloading", "step": 0, "clips": [], "error": None}
    
    video_path = UPLOAD_DIR / f"{job_id}_video.mp4"
    
    # Download with yt-dlp
    cmd = [
        "yt-dlp",
        "-f", "best[filesize<50M]/best",
        "-o", str(video_path),
        "--max-filesize", "50M",
        url
    ]
    
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail="Could not download video. Please check the URL.")
    
    jobs[job_id]["status"] = "transcribing"
    jobs[job_id]["step"] = 1
    asyncio.create_task(process_video(job_id, video_path, style))
    return {"job_id": job_id, "status": "processing"}

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

@app.get("/stream/{job_id}/{filename}")
def stream_clip(job_id: str, filename: str):
    """Stream video for preview"""
    file_path = UPLOAD_DIR / job_id / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    def iterfile():
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk
    
    return StreamingResponse(
        iterfile(),
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Content-Disposition": "inline"}
    )

def get_video_duration(video_path):
    try:
        result = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)], capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except:
        return 60.0

async def process_video(job_id: str, video_path: Path, style: str):
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        jobs[job_id] = {**jobs[job_id], "status": "transcribing", "step": 1}
        
        with open(video_path, "rb") as f:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=f, response_format="verbose_json", timestamp_granularities=["segment"])

        duration_total = get_video_duration(video_path)
        jobs[job_id] = {**jobs[job_id], "status": "scoring", "step": 2}
        
        segments = getattr(transcript, 'segments', [])
        if segments:
            segments_text = "\n".join([
                f"[{s['start']:.1f}s - {s['end']:.1f}s]: {s['text']}" if isinstance(s, dict)
                else f"[{s.start:.1f}s - {s.end:.1f}s]: {s.text}"
                for s in segments[:50]
            ])
        else:
            segments_text = f"Video duration: {duration_total:.0f} seconds."

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"""Analyze this video and create 5 viral short clips.
Style: {style}
Duration: {duration_total:.0f}s
Transcript: {segments_text}

Return ONLY valid JSON array:
[{{"start":5.0,"end":35.0,"hook_title":"TITLE","hook_subtitle":"SUBTITLE","viral_score":90,"viral_level":"Very High"}}]
Rules: clips 15-45s, ALL CAPS titles max 4 words, spread throughout video, end <= {duration_total:.0f}"""}],
            temperature=0.3, max_tokens=600
        )

        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
            if raw.startswith("json"): raw = raw[4:]
        start_i = raw.find('['); end_i = raw.rfind(']') + 1
        if start_i >= 0 and end_i > start_i: raw = raw[start_i:end_i]
        
        try:
            clips_data = json.loads(raw.strip())
        except:
            seg = duration_total / 5
            clips_data = [{"start": i*seg+2, "end": min(i*seg+32, duration_total-1), "hook_title": t, "hook_subtitle": s, "viral_score": sc, "viral_level": l}
                for i, (t, s, sc, l) in enumerate([("KEY INSIGHT","WATCH NOW",92,"Very High"),("POWERFUL MOMENT","SHARE THIS",87,"High"),("VIRAL HOOK","MUST SEE",82,"High"),("BEST PART","EPIC CLIP",77,"Medium"),("TOP HIGHLIGHT","DON'T MISS",72,"Medium")])]

        jobs[job_id] = {**jobs[job_id], "status": "cutting", "step": 3}
        output_dir = UPLOAD_DIR / job_id
        output_dir.mkdir(exist_ok=True)
        clips = []

        for i, clip in enumerate(clips_data[:5]):
            try:
                output_file = output_dir / f"clip_{i+1}.mp4"
                start = max(0, float(clip.get("start", 0)))
                end = float(clip.get("end", start + 30))
                duration = min(max(end - start, 10), 60)
                if start + duration > duration_total: start = max(0, duration_total - duration - 1)

                cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(video_path), "-t", str(duration),
                       "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
                       "-c:v", "libx264", "-c:a", "aac", "-preset", "ultrafast", "-crf", "28", str(output_file)]
                
                result = subprocess.run(cmd, capture_output=True, timeout=180)
                if result.returncode == 0 and output_file.exists():
                    score = int(clip.get("viral_score", 80))
                    color = "#34d399" if score >= 90 else "#a78bfa" if score >= 80 else "#fbbf24" if score >= 70 else "#fb923c"
                    clips.append({
                        "id": i+1, "filename": f"clip_{i+1}.mp4",
                        "download_url": f"/download/{job_id}/clip_{i+1}.mp4",
                        "stream_url": f"/stream/{job_id}/clip_{i+1}.mp4",
                        "hook_title": str(clip.get("hook_title", f"CLIP {i+1}")),
                        "hook_subtitle": str(clip.get("hook_subtitle", "WATCH NOW")),
                        "viral_score": score, "viral_level": str(clip.get("viral_level", "High")),
                        "duration": f"{int(duration//60)}:{int(duration%60):02d}", "color": color
                    })
            except Exception as e:
                print(f"Clip {i+1} error: {e}")
                continue

        jobs[job_id] = {"status": "done", "step": 5, "clips": clips, "error": None} if clips else {"status": "failed", "step": 0, "clips": [], "error": "Could not generate clips"}
        try: video_path.unlink(missing_ok=True)
        except: pass

    except Exception as e:
        jobs[job_id] = {**jobs[job_id], "status": "failed", "error": str(e), "clips": []}
