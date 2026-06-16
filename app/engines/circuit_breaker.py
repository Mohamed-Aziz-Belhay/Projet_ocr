"""
app/engines/circuit_breaker.py
Per-engine circuit breaker (CLOSED → OPEN → HALF_OPEN).
Thread-safe. Zero hard dependencies beyond stdlib.
"""
from __future__ import annotations
import threading
import time
from enum import IntEnum
from typing import Dict, Optional

from app.core.logging import get_logger

log = get_logger(__name__)


class CBState(IntEnum):
    CLOSED    = 0
    OPEN      = 1
    HALF_OPEN = 2


class CircuitBreaker:
    def __init__(self, engine_name: str, threshold: int, timeout: int):
        self.engine_name = engine_name
        self.threshold   = threshold
        self.timeout     = timeout
        self._state      = CBState.CLOSED
        self._failures   = 0
        self._opened_at: Optional[float] = None
        self._lock       = threading.RLock()

    @property
    def state(self) -> CBState:
        with self._lock:
            if self._state == CBState.OPEN:
                if time.time() - (self._opened_at or 0) >= self.timeout:
                    self._state = CBState.HALF_OPEN
                    log.info("Circuit half-open", extra={"engine": self.engine_name})
                    self._emit_metric()
            return self._state

    def is_available(self) -> bool:
        return self.state in (CBState.CLOSED, CBState.HALF_OPEN)

    def record_success(self) -> None:
        with self._lock:
            if self._state == CBState.HALF_OPEN:
                log.info("Circuit closed after recovery", extra={"engine": self.engine_name})
                self._state    = CBState.CLOSED
                self._failures = 0
                self._opened_at = None
                self._emit_metric()

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == CBState.HALF_OPEN or self._failures >= self.threshold:
                self._state     = CBState.OPEN
                self._opened_at = time.time()
                log.warning(
                    "Circuit opened",
                    extra={"engine": self.engine_name, "failures": self._failures},
                )
                self._emit_metric()

    def _emit_metric(self) -> None:
        try:
            from app.core.metrics import circuit_breaker_state
            circuit_breaker_state.labels(engine=self.engine_name).set(int(self._state))
        except Exception:
            pass

    def reset(self) -> None:
        with self._lock:
            self._state     = CBState.CLOSED
            self._failures  = 0
            self._opened_at = None


# ── Registry ──────────────────────────────────────────────────────────────────

_breakers: Dict[str, CircuitBreaker] = {}
_lock = threading.Lock()


def get_circuit_breaker(engine_name: str) -> CircuitBreaker:
    with _lock:
        if engine_name not in _breakers:
            from app.core.settings import get_settings
            s = get_settings()
            _breakers[engine_name] = CircuitBreaker(
                engine_name=engine_name,
                threshold=s.CIRCUIT_BREAKER_THRESHOLD,
                timeout=s.CIRCUIT_BREAKER_TIMEOUT,
            )
        return _breakers[engine_name]


def all_breaker_states() -> Dict[str, str]:
    return {name: CBState(b.state).name for name, b in _breakers.items()}