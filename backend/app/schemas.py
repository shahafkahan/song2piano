from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobEventOut(BaseModel):
    id: int
    job_id: str
    stage: str
    progress: int
    status: str
    message: str
    device: str | None = None
    created_at: datetime


class ArtifactUrls(BaseModel):
    audio: str | None = None
    musicxml: str | None = None
    pdf: str | None = None
    midi: str | None = None
    quantized_midi: str | None = None
    notes: str | None = None
    stems: dict[str, str] = Field(default_factory=dict)


class JobSummary(BaseModel):
    id: str
    title: str
    original_filename: str
    status: str
    stage: str
    progress: int
    message: str
    error_message: str | None = None
    instrument: str
    separate_stems: bool
    stem_mode: str = "auto"
    quantization: str
    detected_bpm: float | None = None
    detected_key: str | None = None
    time_signature: str
    duration_seconds: float | None = None
    note_count: int | None = None
    measure_count: int | None = None
    compute_device: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class JobDetail(JobSummary):
    requested_bpm: float | None = None
    requested_time_signature: str
    requested_key_signature: str | None = None
    artifacts: ArtifactUrls = Field(default_factory=ArtifactUrls)
    events: list[JobEventOut] = Field(default_factory=list)


class WorkerInfo(BaseModel):
    worker_online: bool
    device: str = "unknown"
    device_name: str = "Checking..."
    cuda_available: bool = False
    mps_available: bool = False
    onnx_providers: list[str] = Field(default_factory=list)
    last_seen: datetime | None = None


class HealthResponse(BaseModel):
    status: str
    database: str
    queue_backend: str
    storage_backend: str
    worker: WorkerInfo


def build_artifact_urls(job_id: str, artifacts_json: dict[str, Any] | None) -> ArtifactUrls:
    data = artifacts_json or {}
    base = f"/api/jobs/{job_id}/files"
    stems_raw = data.get("stems") or {}
    stems_urls = {name: f"{base}/stems/{name}" for name in stems_raw.keys()}

    return ArtifactUrls(
        audio=f"{base}/audio" if (data.get("playable_audio") or data.get("original_audio")) else None,
        musicxml=f"{base}/musicxml" if data.get("musicxml") else None,
        pdf=f"{base}/pdf" if data.get("pdf") else None,
        midi=f"{base}/midi" if data.get("midi") else None,
        quantized_midi=f"{base}/quantized_midi" if data.get("quantized_midi") else None,
        notes=f"{base}/notes" if data.get("notes_json") else None,
        stems=stems_urls,
    )
