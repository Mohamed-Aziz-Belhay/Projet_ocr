"""
app/core/metrics.py
Business-level Prometheus metrics.
These go BEYOND HTTP counters — they measure extraction quality.

Exposed at /metrics alongside the FastAPI instrumentator metrics.
"""
from __future__ import annotations
from typing import Optional

try:
    from prometheus_client import Counter, Histogram, Gauge
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False


def _noop(*args, **kwargs):
    class _Noop:
        def labels(self, **kw): return self
        def inc(self, *a, **k): pass
        def observe(self, *a, **k): pass
        def set(self, *a, **k): pass
    return _Noop()


if _PROM_AVAILABLE:
    ocr_extractions_total = Counter(
        "ocr_extractions_total",
        "Total extraction requests",
        ["org_slug", "template_id", "engine", "status"],
    )
    ocr_confidence_histogram = Histogram(
        "ocr_extraction_confidence",
        "Global confidence score per extraction",
        ["template_id", "engine"],
        buckets=[0.1, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0],
    )
    ocr_processing_duration = Histogram(
        "ocr_processing_duration_seconds",
        "End-to-end extraction duration in seconds",
        ["template_id", "engine"],
        buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
    )
    ocr_field_extraction_total = Counter(
        "ocr_field_extraction_total",
        "Per-field extraction outcomes",
        ["template_id", "field_name", "outcome"],
    )
    ocr_pages_processed_total = Counter(
        "ocr_pages_processed_total",
        "Total pages processed",
        ["org_slug", "engine"],
    )
    ocr_queue_depth = Gauge(
        "ocr_job_queue_depth",
        "Number of jobs currently queued",
    )
    ocr_quota_usage = Gauge(
        "ocr_quota_usage_pct",
        "Monthly quota usage percentage",
        ["org_slug", "quota_type"],
    )
    circuit_breaker_state = Gauge(
        "ocr_circuit_breaker_state",
        "Circuit breaker state (0=closed, 1=open, 2=half-open)",
        ["engine"],
    )
    rate_limit_hits_total = Counter(
        "ocr_rate_limit_hits_total",
        "Rate limit rejections",
        ["org_slug"],
    )
else:
    ocr_extractions_total     = _noop()
    ocr_confidence_histogram  = _noop()
    ocr_processing_duration   = _noop()
    ocr_field_extraction_total = _noop()
    ocr_pages_processed_total = _noop()
    ocr_queue_depth           = _noop()
    ocr_quota_usage           = _noop()
    circuit_breaker_state     = _noop()
    rate_limit_hits_total     = _noop()


def record_extraction(
    org_slug: str,
    template_id: Optional[str],
    engine: str,
    status: str,
    confidence: float,
    duration_seconds: float,
    page_count: int,
    fields: list,
) -> None:
    tid = template_id or "auto"
    ocr_extractions_total.labels(
        org_slug=org_slug, template_id=tid, engine=engine, status=status
    ).inc()
    ocr_confidence_histogram.labels(template_id=tid, engine=engine).observe(confidence)
    ocr_processing_duration.labels(template_id=tid, engine=engine).observe(duration_seconds)
    ocr_pages_processed_total.labels(org_slug=org_slug, engine=engine).inc(page_count)
    for field in fields:
        if field.value is None:
            outcome = "missing"
        elif not field.validated:
            outcome = "invalid"
        else:
            outcome = "found"
        ocr_field_extraction_total.labels(
            template_id=tid, field_name=field.name, outcome=outcome,
        ).inc()


def update_quota_metrics(org_slug: str, pages_used: int, pages_quota: int,
                          jobs_used: int, jobs_quota: int) -> None:
    if pages_quota > 0:
        ocr_quota_usage.labels(org_slug=org_slug, quota_type="pages").set(
            pages_used / pages_quota * 100
        )
    if jobs_quota > 0:
        ocr_quota_usage.labels(org_slug=org_slug, quota_type="jobs").set(
            jobs_used / jobs_quota * 100
        )