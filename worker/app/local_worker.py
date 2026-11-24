from __future__ import annotations

import logging
import signal
import sys
import time

from sqlalchemy import asc, select

from app.ml.device import resolve_device
from app.ml.pipeline import run_transcription_job
from songscribe_shared.config import get_settings
from songscribe_shared.db import get_session_factory, init_db
from songscribe_shared.models import Job, JobStatus, WorkerHeartbeat, utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("songscribe.local_worker")

_RUNNING = True


def _handle_signal(_signum, _frame) -> None:
    global _RUNNING
    _RUNNING = False


def _update_heartbeat(worker_id: str = "local-worker") -> None:
    settings = get_settings()
    info = resolve_device(settings.device)
    with get_session_factory()() as db:
        hb = db.get(WorkerHeartbeat, worker_id)
        if hb is None:
            hb = WorkerHeartbeat(
                worker_id=worker_id,
                device=info.device,
                device_name=info.device_name,
                cuda_available=info.cuda_available,
                mps_available=info.mps_available,
                onnx_providers=",".join(info.onnx_providers),
                updated_at=utcnow(),
            )
            db.add(hb)
        else:
            hb.device = info.device
            hb.device_name = info.device_name
            hb.cuda_available = info.cuda_available
            hb.mps_available = info.mps_available
            hb.onnx_providers = ",".join(info.onnx_providers)
            hb.updated_at = utcnow()
        db.commit()


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    settings = get_settings()
    init_db()
    info = resolve_device(settings.device)
    logger.info("Local ML worker started on %s | ONNX=%s", info.label, info.onnx_providers)

    last_hb = 0.0
    while _RUNNING:
        now = time.monotonic()
        if now - last_hb >= 5.0:
            try:
                _update_heartbeat()
                last_hb = now
            except Exception as exc:
                logger.warning("Heartbeat update failed: %s", exc)

        job_id: str | None = None
        with get_session_factory()() as db:
            job = db.execute(
                select(Job)
                .where(Job.status == JobStatus.QUEUED.value)
                .order_by(asc(Job.created_at))
                .limit(1)
            ).scalar_one_or_none()
            if job is not None:
                job.status = JobStatus.PROCESSING.value
                job.stage = "preparing"
                job.updated_at = utcnow()
                db.commit()
                job_id = job.id

        if job_id is not None:
            logger.info("Processing job %s", job_id)
            try:
                run_transcription_job(job_id)
                logger.info("Completed job %s", job_id)
            except Exception as exc:
                logger.exception("Job %s failed: %s", job_id, exc)
            continue

        time.sleep(0.8)

    logger.info("Local ML worker shutting down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
