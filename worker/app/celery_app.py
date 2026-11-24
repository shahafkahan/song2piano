from __future__ import annotations

from celery import Celery

from songscribe_shared.config import get_settings

settings = get_settings()

celery_app = Celery(
    "songscribe",
    broker=settings.effective_broker_url,
    backend=settings.effective_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_default_queue="transcription",
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)
