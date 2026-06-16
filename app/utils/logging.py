"""
app/utils/logging.py
Extra logging utilities — structured context managers, decorators,
and helpers for timing & audit trails.
"""
from __future__ import annotations
import functools
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, Optional

from app.core.logging import get_logger

log = get_logger("utils.logging")


# ── Timing context manager ────────────────────────────────────────────────────

@contextmanager
def timed(label: str, extra: Optional[Dict[str, Any]] = None) -> Generator[dict, None, None]:
    """
    Context manager that measures elapsed time and logs it.

    Usage:
        with timed("ocr_pipeline", extra={"engine": "paddle"}) as t:
            result = run_pipeline(...)
        print(t["elapsed_ms"])
    """
    ctx: dict = {"label": label, "elapsed_ms": 0}
    t0 = time.time()
    try:
        yield ctx
    finally:
        ctx["elapsed_ms"] = int((time.time() - t0) * 1000)
        payload = {"label": label, "elapsed_ms": ctx["elapsed_ms"]}
        if extra:
            payload.update(extra)
        log.debug("Timed block complete", extra=payload)


# ── Function decorator ────────────────────────────────────────────────────────

def log_call(logger_name: Optional[str] = None):
    """
    Decorator: logs entry + exit of a function with elapsed time.
    Useful for service-level methods.

    Usage:
        @log_call()
        def extract_sync(self, file_path, request):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        _log = get_logger(logger_name or fn.__module__)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            _log.debug(f"→ {fn.__qualname__}")
            t0 = time.time()
            try:
                result = fn(*args, **kwargs)
                elapsed = int((time.time() - t0) * 1000)
                _log.debug(f"← {fn.__qualname__}", extra={"elapsed_ms": elapsed})
                return result
            except Exception as exc:
                elapsed = int((time.time() - t0) * 1000)
                _log.error(
                    f"✗ {fn.__qualname__} raised {type(exc).__name__}",
                    extra={"elapsed_ms": elapsed, "error": str(exc)},
                )
                raise
        return wrapper
    return decorator


# ── Audit logger ─────────────────────────────────────────────────────────────

class AuditLogger:
    """
    Structured audit trail for security-relevant events.
    Logs to a dedicated 'audit' logger — can be routed to a separate sink.
    """
    log = get_logger("audit")

    @classmethod
    def extraction_request(
        cls,
        api_key: str,
        template_id: Optional[str],
        file_name: str,
        job_id: str,
    ) -> None:
        cls.log.info(
            "EXTRACTION_REQUEST",
            extra={
                "api_key_prefix": api_key[:8] + "..",
                "template_id": template_id,
                "file_name": file_name,
                "job_id": job_id,
            },
        )

    @classmethod
    def template_modified(
        cls,
        api_key: str,
        action: str,
        template_id: str,
    ) -> None:
        cls.log.warning(
            "TEMPLATE_MODIFIED",
            extra={
                "api_key_prefix": api_key[:8] + "..",
                "action": action,  # create | update | delete
                "template_id": template_id,
            },
        )

    @classmethod
    def auth_failure(cls, api_key_provided: str, path: str) -> None:
        cls.log.warning(
            "AUTH_FAILURE",
            extra={
                "key_prefix": (api_key_provided or "")[:8],
                "path": path,
            },
        )