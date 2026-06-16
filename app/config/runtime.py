"""
app/config/runtime.py
Runtime configuration — values that can be changed without restarting.
Unlike Settings (env-based), these can be updated via API or admin UI.
Stored in memory; persisted to runtime_config.json if PERSIST_RUNTIME=true.
"""
from __future__ import annotations
import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.logging import get_logger

log = get_logger(__name__)

_DEFAULTS: Dict[str, Any] = {
    # OCR behaviour
    "global_fast_mode":              False,
    "default_engine_override":       None,      # None → use template/settings default
    "min_confidence_threshold":      0.0,       # discard fields below this score
    "max_pages_per_doc":             20,
    # Extraction
    "enable_auto_template_detection": True,
    "auto_detect_min_score":         2,         # minimum anchor score to trust detection
    "fallback_to_raw_text":          True,      # return raw text if no template matches
    # Post-processing
    "enable_postprocess_hooks":      True,
    # Rate limiting (per API key, requests/minute, 0 = unlimited)
    "rate_limit_per_minute":         0,
    # Maintenance
    "maintenance_mode":              False,
    "maintenance_message":           "Service temporarily unavailable",
}


class RuntimeConfig:
    _lock = threading.RLock()

    def __init__(self, persist_path: Optional[str] = None):
        self._config: Dict[str, Any] = dict(_DEFAULTS)
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path and self._persist_path.exists():
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            with self._lock:
                self._config.update(data)
            log.info("Runtime config loaded from disk", extra={"path": str(self._persist_path)})
        except Exception as exc:
            log.error("Failed to load runtime config", extra={"error": str(exc)})

    def _save_to_disk(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(self._config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.error("Failed to save runtime config", extra={"error": str(exc)})

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if key not in _DEFAULTS:
            raise ValueError(f"Unknown runtime config key: '{key}'. Allowed: {list(_DEFAULTS)}")
        with self._lock:
            self._config[key] = value
            self._save_to_disk()
        log.info("Runtime config updated", extra={"key": key, "value": value})

    def set_many(self, updates: Dict[str, Any]) -> None:
        for key in updates:
            if key not in _DEFAULTS:
                raise ValueError(f"Unknown runtime config key: '{key}'")
        with self._lock:
            self._config.update(updates)
            self._save_to_disk()

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def reset(self) -> None:
        with self._lock:
            self._config = dict(_DEFAULTS)
            self._save_to_disk()
        log.info("Runtime config reset to defaults")

    # ── Convenience properties ─────────────────────────────────────────────────

    @property
    def fast_mode(self) -> bool:
        return bool(self.get("global_fast_mode", False))

    @property
    def maintenance_mode(self) -> bool:
        return bool(self.get("maintenance_mode", False))

    @property
    def auto_detect_enabled(self) -> bool:
        return bool(self.get("enable_auto_template_detection", True))

    @property
    def min_confidence(self) -> float:
        return float(self.get("min_confidence_threshold", 0.0))


# ── Singleton ──────────────────────────────────────────────────────────────────

_runtime: Optional[RuntimeConfig] = None


def get_runtime_config() -> RuntimeConfig:
    global _runtime
    if _runtime is None:
        _runtime = RuntimeConfig(persist_path="/tmp/ocr_runtime_config.json")
    return _runtime