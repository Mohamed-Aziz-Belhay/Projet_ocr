"""test_rate_limit.py — à lancer avec le serveur uvicorn démarré."""
import requests
from collections import Counter

URL = "http://localhost:8000/extract"
HEADERS = {"X-API-Key": "dev-key-123", "Origin": "http://localhost:4200"}

codes = []
cors_header_on_429 = None

for _ in range(80):  # au-delà de RPM(60) + BURST(10) = 70
    r = requests.get(URL, headers=HEADERS)
    codes.append(r.status_code)
    if r.status_code == 429 and cors_header_on_429 is None:
        cors_header_on_429 = r.headers.get("access-control-allow-origin")

print(Counter(codes))
print("Header CORS présent sur la réponse 429 :", cors_header_on_429)