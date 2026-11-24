from __future__ import annotations

import threading
import time

from celery.signals import worker_ready

from app.celery_app import celery_app
from app.ml.device import resolve_device
from app.ml.pipeline import run_transcription_job
from songscribe_shared.config import get_settings
from songscribe_shared.db import get_session_factory, init_db
from songscribe_shared.models import WorkerHeartbeat, utcnow


def upsert_heartbeat(worker_id: str = "celery-worker") -> None:
    settings = get_settings()
    info = resolve_device(settings.device)
    init_db()
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


@worker_ready.connect
def _start_heartbeat_thread(**_kwargs) -> None:
    def _loop() -> None:
        while True:
            try:
                upsert_heartbeat("celery-worker")
            except Exception:
                pass
            time.sleep(10)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


@celery_app.task(name="worker.transcribe_audio", bind=True)
def transcribe_audio_task(self, job_id: str) -> dict[str, str]:
    upsert_heartbeat("celery-worker")
    run_transcription_job(job_id)
    return {"job_id": job_id, "status": "completed"}
