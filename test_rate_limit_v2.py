"""test_rate_limit_v2.py — envoie le batch ET interroge Redis dans le même process, sans délai."""
import requests
import redis
from collections import Counter

URL = "http://localhost:8000/extract"
HEADERS = {"X-API-Key": "dev-key-123", "Origin": "http://localhost:4200"}

codes = []
cors_header_on_429 = None

for _ in range(80):
    r = requests.get(URL, headers=HEADERS)
    codes.append(r.status_code)
    if r.status_code == 429 and cors_header_on_429 is None:
        cors_header_on_429 = r.headers.get("access-control-allow-origin")

print("Codes HTTP:", Counter(codes))
print("Header CORS sur 429:", cors_header_on_429)

# Interrogation Redis IMMÉDIATE, dans le même script, aucune ambiguïté de timing
rc = redis.from_url("redis://localhost:6379/0", decode_responses=True)
key = "rl:dev-key-123"
print(f"\nZCARD('{key}') = {rc.zcard(key)}   (attendu ≈ 80)")
print(f"TTL('{key}') = {rc.ttl(key)} secondes")