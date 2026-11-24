from __future__ import annotations

from functools import lru_cache

from celery import Celery

from songscribe_shared.config import get_settings


@lru_cache
def get_celery_client() -> Celery:
    settings = get_settings()
    app = Celery(
        "songscribe",
        broker=settings.effective_broker_url,
        backend=settings.effective_result_backend,
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_default_queue="transcription",
    )
    return app


def enqueue_transcription(job_id: str) -> None:
    settings = get_settings()
    if settings.queue_backend == "celery":
        get_celery_client().send_task(
            "worker.transcribe_audio",
            args=[job_id],
            queue="transcription",
        )
    # In queue_backend=local mode, worker polls DB for queued jobs.
