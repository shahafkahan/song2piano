from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Instrument(str, enum.Enum):
    PIANO = "piano"
    GUITAR = "guitar"
    VOCALS = "vocals"
    BASS = "bass"
    STRINGS = "strings"
    OTHER = "other"


class QuantizationLevel(str, enum.Enum):
    AUTO = "auto"
    QUARTER = "1/4"
    EIGHTH = "1/8"
    SIXTEENTH = "1/16"
    THIRTY_SECOND = "1/32"
    TRIPLET = "triplet"


class StemMode(str, enum.Enum):
    PIANO_COVER = "piano_cover"
    PIANO_ONLY = "piano_only"
    AUTO = "auto"
    NONE = "none"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED.value, index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(512), default="Queued for transcription")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    instrument: Mapped[str] = mapped_column(String(32), default=Instrument.PIANO.value)
    separate_stems: Mapped[bool] = mapped_column(Boolean, default=True)
    stem_mode: Mapped[str] = mapped_column(String(32), default=StemMode.AUTO.value)
    quantization: Mapped[str] = mapped_column(String(16), default=QuantizationLevel.AUTO.value)

    requested_bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_time_signature: Mapped[str] = mapped_column(String(16), default="auto")
    requested_key_signature: Mapped[str | None] = mapped_column(String(32), nullable=True)

    detected_bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    time_signature: Mapped[str] = mapped_column(String(16), default="4/4")
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    note_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measure_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compute_device: Mapped[str | None] = mapped_column(String(64), nullable=True)

    artifacts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[JobEvent]] = relationship(
        "JobEvent",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobEvent.id",
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    device: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship("Job", back_populates="events")


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device: Mapped[str] = mapped_column(String(32), nullable=False)
    device_name: Mapped[str] = mapped_column(String(128), nullable=False)
    cuda_available: Mapped[bool] = mapped_column(Boolean, default=False)
    mps_available: Mapped[bool] = mapped_column(Boolean, default=False)
    onnx_providers: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
