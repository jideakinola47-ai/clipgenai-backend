import requests

VIZARD_API_KEY = "76f3b8d194804562a7fb22584dbd2361"

headers = {
    "Content-Type": "application/json",
    "VIZARDAI_API_KEY": VIZARD_API_KEY
}

payload = {
    "videoUrl": "https://res.cloudinary.com/de5jdqth5/video/upload/v1777710389/mtkajugij9qmcqbfmusf.mp4",
    "videoType": 1,
    "ext": "mp4",
    "lang": "en",
    "preferLength": [1],
    "ratioOfClip": 1,
    "subtitleSwitch": 1,
    "maxClipNumber": 3
}

res = requests.post(
    "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/create",
    headers=headers,
    json=payload,
    timeout=30
)
print("Status:", res.status_code)
print("Response:", res.text)