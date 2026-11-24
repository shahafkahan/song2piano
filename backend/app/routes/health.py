from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from app.schemas import HealthResponse, WorkerInfo
from songscribe_shared.config import get_settings
from songscribe_shared.db import get_db
from songscribe_shared.models import WorkerHeartbeat

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    settings = get_settings()
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    worker_info = WorkerInfo(worker_online=False)
    hb = db.execute(
        select(WorkerHeartbeat).order_by(desc(WorkerHeartbeat.updated_at)).limit(1)
    ).scalar_one_or_none()

    if hb is not None:
        now = datetime.now(timezone.utc)
        updated = hb.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        online = (now - updated) <= timedelta(seconds=35)
        providers = [p for p in (hb.onnx_providers or "").split(",") if p]
        worker_info = WorkerInfo(
            worker_online=online,
            device=hb.device,
            device_name=hb.device_name,
            cuda_available=hb.cuda_available,
            mps_available=hb.mps_available,
            onnx_providers=providers,
            last_seen=updated,
        )

    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        database=db_status,
        queue_backend=settings.queue_backend,
        storage_backend=settings.storage_backend,
        worker=worker_info,
    )
