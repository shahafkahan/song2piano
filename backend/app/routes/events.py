from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import redis.asyncio as aioredis
from sqlalchemy import select

from songscribe_shared.config import get_settings
from songscribe_shared.db import get_session_factory
from songscribe_shared.events import job_event_channel
from songscribe_shared.models import Job, JobEvent, JobStatus

router = APIRouter(prefix="/jobs", tags=["events"])

TERMINAL_STATUSES = {JobStatus.COMPLETED.value, JobStatus.FAILED.value}


def _format_sse(event_name: str, data: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data)}\n\n"


def _event_to_dict(ev: JobEvent, error_message: str | None = None) -> dict:
    return {
        "id": ev.id,
        "job_id": ev.job_id,
        "stage": ev.stage,
        "progress": ev.progress,
        "status": ev.status,
        "message": ev.message,
        "device": ev.device,
        "error_message": error_message,
        "created_at": ev.created_at.isoformat(),
    }


@router.get("/{job_id}/events")
async def stream_job_events(job_id: str) -> StreamingResponse:
    session_factory = get_session_factory()
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        initial_events = (
            db.execute(
                select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id.asc())
            )
            .scalars()
            .all()
        )
        initial_status = job.status
        initial_error = job.error_message
        initial_payloads = [_event_to_dict(ev, initial_error) for ev in initial_events]
        last_event_id = initial_events[-1].id if initial_events else 0

    settings = get_settings()

    async def event_generator() -> AsyncIterator[str]:
        for payload in initial_payloads:
            yield _format_sse("progress", payload)

        if initial_status in TERMINAL_STATUSES:
            yield _format_sse("done", {"job_id": job_id, "status": initial_status})
            return

        if settings.queue_backend == "celery" and settings.redis_url:
            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            pubsub = client.pubsub()
            channel = job_event_channel(job_id)
            await pubsub.subscribe(channel)
            last_keepalive = time.monotonic()
            try:
                while True:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg and msg.get("data"):
                        data = json.loads(msg["data"])
                        yield _format_sse("progress", data)
                        if data.get("status") in TERMINAL_STATUSES:
                            yield _format_sse(
                                "done", {"job_id": job_id, "status": data.get("status")}
                            )
                            break
                    elif time.monotonic() - last_keepalive >= 10.0:
                        last_keepalive = time.monotonic()
                        yield ": keepalive\n\n"
                        # Safety check in case worker finished before subscription attached
                        with session_factory() as db:
                            cur = db.get(Job, job_id)
                            if cur and cur.status in TERMINAL_STATUSES:
                                yield _format_sse(
                                    "progress",
                                    {
                                        "id": last_event_id,
                                        "job_id": job_id,
                                        "stage": cur.stage,
                                        "progress": cur.progress,
                                        "status": cur.status,
                                        "message": cur.message,
                                        "device": cur.compute_device,
                                        "error_message": cur.error_message,
                                    },
                                )
                                yield _format_sse(
                                    "done", {"job_id": job_id, "status": cur.status}
                                )
                                break
            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
                await client.aclose()
        else:
            seen_id = last_event_id
            last_keepalive = time.monotonic()
            while True:
                await asyncio.sleep(0.4)
                with session_factory() as db:
                    cur = db.get(Job, job_id)
                    if cur is None:
                        break
                    new_events = (
                        db.execute(
                            select(JobEvent)
                            .where(JobEvent.job_id == job_id, JobEvent.id > seen_id)
                            .order_by(JobEvent.id.asc())
                        )
                        .scalars()
                        .all()
                    )
                    for ev in new_events:
                        seen_id = ev.id
                        yield _format_sse("progress", _event_to_dict(ev, cur.error_message))

                    if cur.status in TERMINAL_STATUSES:
                        yield _format_sse("done", {"job_id": job_id, "status": cur.status})
                        break

                if time.monotonic() - last_keepalive >= 12.0:
                    last_keepalive = time.monotonic()
                    yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
