"""
app/worker/celery_app.py
Celery application — async job processing.
Workers run independently from the FastAPI process.
"""
from __future__ import annotations

from celery import Celery
from app.core.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "ocr_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timeouts
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,

    # Reliability
    task_acks_late=True,               # ack after completion, not on receipt
    task_reject_on_worker_lost=True,   # requeue if worker dies mid-task
    worker_prefetch_multiplier=1,      # one task at a time per worker thread

    # Retry
    task_max_retries=3,
    task_default_retry_delay=30,       # seconds

    # Result expiry
    result_expires=3600,               # 1 hour

    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,

    # Beat schedule (periodic tasks)
    beat_schedule={
        "purge-expired-jobs": {
            "task": "app.worker.tasks.purge_expired_data",
            "schedule": 3600.0,        # every hour
        },
        "reset-monthly-quotas": {
            "task": "app.worker.tasks.reset_monthly_quotas",
            "schedule": 86400.0,       # daily
        },
        "anonymize-old-logs": {
            "task": "app.worker.tasks.anonymize_old_audit_logs",
            "schedule": 3600.0,
        },
    },
)