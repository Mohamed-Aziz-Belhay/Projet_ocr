"""
app/api/rate_limiter.py
Sliding-window rate limiter backed by Redis.
Falls back gracefully when Redis is unavailable (allow request, log warning).

Algorithm: Redis sorted set with timestamps as scores.
  ZADD key:window <now_ms> <request_id>
  ZREMRANGEBYSCORE key:window 0 <now_ms - window_ms>
  ZCARD key:window → request count in window
  EXPIRE key:window <window_seconds + 1>
"""
from __future__ import annotations
import time
import uuid
from typing import Optional, Tuple

from app.core.settings import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
settings = get_settings()

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not settings.REDIS_URL:
        return None
    try:
        import redis
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception as exc:
        log.warning("Redis unavailable for rate limiting", extra={"error": str(exc)})
        return None


def check_rate_limit(
    api_key_prefix: str,
    limit_rpm: int,
    burst: Optional[int] = None,
) -> Tuple[bool, dict]:
    """
    Check and record a request against the rate limit.

    Returns:
        (allowed: bool, headers: dict)
        headers contains X-RateLimit-* values for the response.
    """
    if not settings.RATE_LIMIT_ENABLED or limit_rpm <= 0:
        return True, {}

    burst = burst or settings.RATE_LIMIT_BURST
    effective_limit = limit_rpm + burst

    r = _get_redis()
    if r is None:
        # Redis unavailable — allow but warn
        return True, {"X-RateLimit-Status": "bypass"}

    window_ms  = 60_000                    # 1 minute in ms
    now_ms     = int(time.time() * 1000)
    window_start = now_ms - window_ms
    key        = f"rl:{api_key_prefix}"
    request_id = str(uuid.uuid4())

    try:
        pipe = r.pipeline(transaction=True)
        pipe.zadd(key, {request_id: now_ms})
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.expire(key, 61)
        results = pipe.execute()
        count = results[2]

        remaining = max(0, effective_limit - count)
        reset_at   = int((now_ms + window_ms) / 1000)

        headers = {
            "X-RateLimit-Limit":     str(effective_limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset":     str(reset_at),
        }

        if count > effective_limit:
            log.warning(
                "Rate limit exceeded",
                extra={"key_prefix": api_key_prefix, "count": count, "limit": effective_limit},
            )
            return False, {**headers, "Retry-After": "60"}

        return True, headers

    except Exception as exc:
        log.error("Rate limiter error", extra={"error": str(exc)})
        return True, {}   # fail open