"""diag_rate_limiter.py — process Python neuf, sans passer par uvicorn."""
from app.core.settings import get_settings
from app.api.rate_limiter import check_rate_limit, _get_redis

settings = get_settings()
print("RATE_LIMIT_ENABLED:", settings.RATE_LIMIT_ENABLED)
print("REDIS_URL:", settings.REDIS_URL)

r = _get_redis()
print("Client Redis obtenu:", r)
if r is not None:
    print("PING direct via le client de l'app:", r.ping())
else:
    print("⚠️ _get_redis() a renvoyé None malgré Memurai actif — voir hypothèses ci-dessous")

blocked_at = None
for i in range(1, 76):
    allowed, headers = check_rate_limit("dev-key-123", settings.RATE_LIMIT_DEFAULT_RPM, settings.RATE_LIMIT_BURST)
    if not allowed:
        blocked_at = i
        print(f"Bloqué à la requête {i}", headers)
        break

if blocked_at is None:
    print("❌ Jamais bloqué sur 75 appels — le rate limiter fail open en continu")
else:
    print(f"✅ Rate limiter fonctionnel — bloqué à la requête {blocked_at} (attendu ≈ 71)")