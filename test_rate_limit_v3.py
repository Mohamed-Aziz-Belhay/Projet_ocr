"""test_rate_limit_v3.py"""
import time
import requests
from collections import Counter

URL = "http://localhost:8000/extract"
HEADERS = {"X-API-Key": "dev-key-123", "Origin": "http://localhost:4200"}

codes = []
start = time.time()
for _ in range(80):
    r = requests.get(URL, headers=HEADERS)
    codes.append(r.status_code)
elapsed = time.time() - start

print("Codes HTTP:", Counter(codes))
print(f"Temps total : {elapsed:.1f} s pour 80 requêtes ({elapsed/80*1000:.0f} ms/requête)")