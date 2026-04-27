# ClipGen.AI Backend

## Setup on Railway

1. Create account at railway.app
2. New Project → Deploy from GitHub
3. Add environment variable:
   - OPENAI_API_KEY = your_key_here
4. Deploy!

## API Endpoints

- GET /health - Check server is running
- POST /upload - Upload video for processing
- GET /status/{job_id} - Check processing status
- GET /download/{job_id}/{filename} - Download clip
