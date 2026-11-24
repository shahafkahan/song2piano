from __future__ import annotations

import json
from typing import Any

import redis
from sqlalchemy.orm import Session

from songscribe_shared.config import get_settings
from songscribe_shared.models import Job, JobEvent, JobStatus, utcnow


def job_event_channel(job_id: str) -> str:
    return f"job:{job_id}:events"


def _get_redis_client() -> redis.Redis | None:
    settings = get_settings()
    if settings.queue_backend != "celery" or not settings.redis_url:
        return None
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def publish_progress(
    db: Session,
    job_id: str,
    stage: str,
    progress: int,
    message: str,
    status: str | None = None,
    device: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Persist job progress to PostgreSQL/SQLite and publish an event to Redis if enabled."""
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError(f"Job not found: {job_id}")

    now = utcnow()
    resolved_status = status or job.status
    clamped_progress = max(0, min(100, int(progress)))

    job.stage = stage
    job.progress = clamped_progress
    job.message = message[:500]
    job.status = resolved_status
    job.updated_at = now

    if device:
        job.compute_device = device
    if error_message is not None:
        job.error_message = error_message
    if resolved_status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
        job.completed_at = now

    ev = JobEvent(
        job_id=job_id,
        stage=stage,
        progress=clamped_progress,
        status=resolved_status,
        message=message[:500],
        device=device or job.compute_device,
        created_at=now,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)

    payload: dict[str, Any] = {
        "id": ev.id,
        "job_id": job_id,
        "stage": ev.stage,
        "progress": ev.progress,
        "status": ev.status,
        "message": ev.message,
        "device": ev.device,
        "error_message": job.error_message,
        "created_at": ev.created_at.isoformat(),
    }

    client = _get_redis_client()
    if client is not None:
        try:
            client.publish(job_event_channel(job_id), json.dumps(payload))
        except Exception:
            pass
        finally:
            client.close()

    return payload
