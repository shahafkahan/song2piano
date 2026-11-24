from __future__ import annotations

from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.celery_client import enqueue_transcription
from app.schemas import JobDetail, JobEventOut, JobSummary, build_artifact_urls
from songscribe_shared.config import get_settings
from songscribe_shared.db import get_db
from songscribe_shared.events import publish_progress
from songscribe_shared.models import Instrument, Job, JobStatus, QuantizationLevel, StemMode
from songscribe_shared.storage import get_storage

router = APIRouter(prefix="/jobs", tags=["jobs"])

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".webm"}
ALLOWED_TIME_SIGNATURES = {"auto", "2/4", "3/4", "4/4", "6/8"}


def _job_to_summary(job: Job) -> JobSummary:
    return JobSummary(
        id=job.id,
        title=job.title,
        original_filename=job.original_filename,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        message=job.message,
        error_message=job.error_message,
        instrument=job.instrument,
        separate_stems=job.separate_stems,
        stem_mode=getattr(job, "stem_mode", "auto") or "auto",
        quantization=job.quantization,
        detected_bpm=job.detected_bpm,
        detected_key=job.detected_key,
        time_signature=job.time_signature,
        duration_seconds=job.duration_seconds,
        note_count=job.note_count,
        measure_count=job.measure_count,
        compute_device=job.compute_device,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _job_to_detail(job: Job) -> JobDetail:
    events = [
        JobEventOut(
            id=ev.id,
            job_id=ev.job_id,
            stage=ev.stage,
            progress=ev.progress,
            status=ev.status,
            message=ev.message,
            device=ev.device,
            created_at=ev.created_at,
        )
        for ev in job.events
    ]
    return JobDetail(
        **_job_to_summary(job).model_dump(),
        requested_bpm=job.requested_bpm,
        requested_time_signature=job.requested_time_signature,
        requested_key_signature=job.requested_key_signature,
        artifacts=build_artifact_urls(job.id, job.artifacts_json),
        events=events,
    )


@router.post("", response_model=JobDetail, status_code=status.HTTP_201_CREATED)
async def create_job(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    instrument: str = Form(default=Instrument.PIANO.value),
    separate_stems: bool = Form(default=True),
    stem_mode: str = Form(default=StemMode.AUTO.value),
    quantization: str = Form(default=QuantizationLevel.AUTO.value),
    bpm: float | None = Form(default=None),
    time_signature: str = Form(default="auto"),
    key_signature: str = Form(default=""),
    db: Session = Depends(get_db),
) -> JobDetail:
    settings = get_settings()

    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing uploaded filename.")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    valid_instruments = {i.value for i in Instrument}
    if instrument not in valid_instruments:
        raise HTTPException(status_code=400, detail=f"Invalid instrument '{instrument}'.")

    valid_stem_modes = {s.value for s in StemMode}
    if stem_mode not in valid_stem_modes:
        raise HTTPException(status_code=400, detail=f"Invalid stem_mode '{stem_mode}'.")

    if stem_mode in (StemMode.PIANO_ONLY.value, StemMode.PIANO_COVER.value):
        instrument = Instrument.PIANO.value
        separate_stems = True
    elif stem_mode == StemMode.NONE.value:
        separate_stems = False

    valid_quants = {q.value for q in QuantizationLevel}
    if quantization not in valid_quants:
        raise HTTPException(status_code=400, detail=f"Invalid quantization '{quantization}'.")

    if time_signature not in ALLOWED_TIME_SIGNATURES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported time signature '{time_signature}'. Allowed: {', '.join(sorted(ALLOWED_TIME_SIGNATURES))}",
        )

    if bpm is not None and (bpm < 30 or bpm > 260):
        raise HTTPException(status_code=400, detail="BPM must be between 30 and 260.")

    raw_bytes = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {settings.max_upload_mb} MB.",
        )

    job_id = str(uuid.uuid4())
    clean_title = title.strip() or Path(file.filename).stem[:120] or "Untitled Score"
    clean_key = key_signature.strip() or None
    input_key = f"jobs/{job_id}/input{ext}"

    storage = get_storage()
    storage.put_bytes(
        input_key,
        raw_bytes,
        content_type=file.content_type or "application/octet-stream",
    )

    job = Job(
        id=job_id,
        title=clean_title,
        original_filename=Path(file.filename).name[:255],
        status=JobStatus.QUEUED.value,
        stage="queued",
        progress=0,
        message="Audio uploaded. Waiting for transcription worker...",
        instrument=instrument,
        separate_stems=separate_stems,
        stem_mode=stem_mode,
        quantization=quantization,
        requested_bpm=bpm,
        requested_time_signature=time_signature,
        requested_key_signature=clean_key,
        time_signature="4/4" if time_signature == "auto" else time_signature,
        artifacts_json={"original_audio": input_key},
    )
    db.add(job)
    db.commit()

    publish_progress(
        db,
        job_id=job_id,
        stage="queued",
        progress=2,
        status=JobStatus.QUEUED.value,
        message="Audio uploaded. Queued for ML transcription pipeline.",
    )

    enqueue_transcription(job_id)
    db.refresh(job)
    return _job_to_detail(job)


@router.get("", response_model=list[JobSummary])
def list_jobs(db: Session = Depends(get_db)) -> list[JobSummary]:
    jobs = db.execute(select(Job).order_by(desc(Job.created_at)).limit(50)).scalars().all()
    return [_job_to_summary(j) for j in jobs]


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobDetail:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_to_detail(job)


@router.post("/{job_id}/extract-piano", response_model=JobDetail, status_code=status.HTTP_201_CREATED)
def extract_piano_only(
    job_id: str,
    quantization: str = Form(default=QuantizationLevel.AUTO.value),
    time_signature: str = Form(default="auto"),
    db: Session = Depends(get_db),
) -> JobDetail:
    src = db.get(Job, job_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source job not found.")

    valid_quants = {q.value for q in QuantizationLevel}
    if quantization not in valid_quants:
        quantization = src.quantization if src.quantization in valid_quants else QuantizationLevel.AUTO.value

    src_artifacts = src.artifacts_json or {}
    src_audio_key = src_artifacts.get("original_audio")
    if not src_audio_key:
        raise HTTPException(status_code=400, detail="Source job is missing original audio.")

    storage = get_storage()
    if not storage.exists(src_audio_key):
        raise HTTPException(status_code=404, detail="Original audio file not found in storage.")

    new_id = str(uuid.uuid4())
    ext = Path(src_audio_key).suffix or ".mp3"
    new_input_key = f"jobs/{new_id}/input{ext}"
    # Copy bytes cleanly to the new job's prefix so deleting one job never breaks the other
    size = storage.get_size(src_audio_key)
    raw_bytes = b"".join(storage.read_range(src_audio_key, 0, size - 1))
    storage.put_bytes(new_input_key, raw_bytes, content_type="application/octet-stream")

    req_ts = time_signature if time_signature in ALLOWED_TIME_SIGNATURES else "auto"
    base_title = src.title.removesuffix(" (Piano Only)").strip()
    new_job = Job(
        id=new_id,
        title=f"{base_title} (Piano Only)"[:255],
        original_filename=src.original_filename,
        status=JobStatus.QUEUED.value,
        stage="queued",
        progress=0,
        message="Extracting isolated piano stem & transcribing piano-only sheets...",
        instrument=Instrument.PIANO.value,
        separate_stems=True,
        stem_mode=StemMode.PIANO_ONLY.value,
        quantization=quantization,
        requested_bpm=src.requested_bpm,
        requested_time_signature=req_ts,
        requested_key_signature=src.requested_key_signature,
        time_signature="4/4" if req_ts == "auto" else req_ts,
        artifacts_json={"original_audio": new_input_key},
    )
    db.add(new_job)
    db.commit()

    publish_progress(
        db,
        job_id=new_id,
        stage="queued",
        progress=2,
        status=JobStatus.QUEUED.value,
        message="Queued for smart Piano-Only isolation and grand-staff transcription.",
    )

    enqueue_transcription(new_id)
    db.refresh(new_job)
    return _job_to_detail(new_job)


@router.post("/{job_id}/piano-cover", response_model=JobDetail, status_code=status.HTTP_201_CREATED)
def create_piano_cover(
    job_id: str,
    quantization: str = Form(default=QuantizationLevel.AUTO.value),
    time_signature: str = Form(default="auto"),
    db: Session = Depends(get_db),
) -> JobDetail:
    src = db.get(Job, job_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source job not found.")

    valid_quants = {q.value for q in QuantizationLevel}
    if quantization not in valid_quants:
        quantization = src.quantization if src.quantization in valid_quants else QuantizationLevel.AUTO.value

    src_artifacts = src.artifacts_json or {}
    src_audio_key = src_artifacts.get("original_audio")
    if not src_audio_key:
        raise HTTPException(status_code=400, detail="Source job is missing original audio.")

    storage = get_storage()
    if not storage.exists(src_audio_key):
        raise HTTPException(status_code=404, detail="Original audio file not found in storage.")

    new_id = str(uuid.uuid4())
    ext = Path(src_audio_key).suffix or ".mp3"
    new_input_key = f"jobs/{new_id}/input{ext}"
    size = storage.get_size(src_audio_key)
    raw_bytes = b"".join(storage.read_range(src_audio_key, 0, size - 1))
    storage.put_bytes(new_input_key, raw_bytes, content_type="application/octet-stream")

    req_ts = time_signature if time_signature in ALLOWED_TIME_SIGNATURES else "auto"
    base_title = (
        src.title.removesuffix(" (Piano Cover)")
        .removesuffix(" (Piano Only)")
        .strip()
    )
    new_job = Job(
        id=new_id,
        title=f"{base_title} (Piano Cover)"[:255],
        original_filename=src.original_filename,
        status=JobStatus.QUEUED.value,
        stage="queued",
        progress=0,
        message="Converting full song (singing melody + accompaniment + bass) to piano...",
        instrument=Instrument.PIANO.value,
        separate_stems=True,
        stem_mode=StemMode.PIANO_COVER.value,
        quantization=quantization,
        requested_bpm=src.requested_bpm,
        requested_time_signature=req_ts,
        requested_key_signature=src.requested_key_signature,
        time_signature="4/4" if req_ts == "auto" else req_ts,
        artifacts_json={"original_audio": new_input_key},
    )
    db.add(new_job)
    db.commit()

    publish_progress(
        db,
        job_id=new_id,
        stage="queued",
        progress=2,
        status=JobStatus.QUEUED.value,
        message="Queued for Full-Song Piano Cover arrangement (vocals + accompaniment + bass).",
    )

    enqueue_transcription(new_id)
    db.refresh(new_job)
    return _job_to_detail(new_job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str, db: Session = Depends(get_db)) -> None:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    storage = get_storage()
    storage.delete_prefix(f"jobs/{job_id}/")
    db.delete(job)
    db.commit()
