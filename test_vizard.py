import requests, json

VIZARD_API_KEY = "d3d058e542074fa89cd861a18c6555d5"

headers = {
    "Content-Type": "application/json",
    "VIZARDAI_API_KEY": VIZARD_API_KEY
}

# Test query endpoint with dummy ID
res = requests.get(
    "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/query/1",
    headers={"VIZARDAI_API_KEY": VIZARD_API_KEY},
    timeout=15
)
print(f"Query test - Status: {res.status_code}, Response: {res.text[:300]}")

# Test create with known public video
payload = {
    "videoUrl": "https://dlany1hql2ufi.cloudfront.net/0-test/WhyHiking-1920x1080-60s.mp4",
    "videoType": 1,
    "ext": "mp4", 
    "lang": "en",
    "preferLength": [1],
    "ratioOfClip": 1,
    "subtitleSwitch": 1,
    "maxClipNumber": 3
}

res2 = requests.post(
    "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project/create",
    headers=headers,
    json=payload,
    timeout=30
)
print(f"Create test - Status: {res2.status_code}, Response: {res2.text[:300]}")
