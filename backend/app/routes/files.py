from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from songscribe_shared.db import get_db
from songscribe_shared.models import Job
from songscribe_shared.storage import get_storage

router = APIRouter(prefix="/jobs", tags=["files"])

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

ARTIFACT_META = {
    "musicxml": ("musicxml", "application/vnd.recordare.musicxml+xml", "score.musicxml"),
    "pdf": ("pdf", "application/pdf", "score.pdf"),
    "midi": ("midi", "audio/midi", "transcription.mid"),
    "quantized_midi": ("quantized_midi", "audio/midi", "quantized.mid"),
    "notes": ("notes_json", "application/json", "notes.json"),
}


def _safe_filename(title: str, suffix: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", title.strip()).strip("_") or "songscribe"
    return f"{slug}-{suffix}"


@router.get("/{job_id}/files/{artifact:path}")
def download_artifact(
    job_id: str,
    artifact: str,
    request: Request,
    download: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    artifacts = job.artifacts_json or {}
    storage_key: str | None = None
    media_type = "application/octet-stream"
    filename = "artifact.bin"

    if artifact == "audio":
        storage_key = artifacts.get("playable_audio") or artifacts.get("original_audio")
        if storage_key and storage_key.endswith(".wav"):
            media_type = "audio/wav"
            filename = _safe_filename(job.title, "audio.wav")
        else:
            media_type = "audio/mpeg"
            filename = _safe_filename(job.title, "audio.mp3")
    elif artifact.startswith("stems/"):
        stem_name = artifact.removeprefix("stems/").strip("/")
        stems = artifacts.get("stems") or {}
        storage_key = stems.get(stem_name)
        media_type = "audio/mpeg"
        filename = _safe_filename(job.title, f"{stem_name}.mp3")
    elif artifact in ARTIFACT_META:
        json_field, media_type, default_suffix = ARTIFACT_META[artifact]
        storage_key = artifacts.get(json_field)
        filename = _safe_filename(job.title, default_suffix)
    else:
        raise HTTPException(status_code=404, detail=f"Unknown artifact '{artifact}'.")

    if not storage_key:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact}' is not available yet.")

    storage = get_storage()
    if not storage.exists(storage_key):
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact}' missing from storage.")

    total_size = storage.get_size(storage_key)
    disposition = "attachment" if download else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{disposition}; filename="{filename}"',
    }

    range_header = request.headers.get("range")
    if range_header:
        match = RANGE_RE.match(range_header.strip())
        if not match:
            raise HTTPException(status_code=416, detail="Invalid Range header.")
        start_s, end_s = match.groups()
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else total_size - 1
        if start >= total_size or end < start:
            raise HTTPException(status_code=416, detail="Requested range not satisfiable.")
        end = min(end, total_size - 1)
        content_length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
        headers["Content-Length"] = str(content_length)
        return StreamingResponse(
            storage.read_range(storage_key, start, end),
            status_code=206,
            media_type=media_type,
            headers=headers,
        )

    headers["Content-Length"] = str(total_size)
    return StreamingResponse(
        storage.read_range(storage_key, 0, total_size - 1),
        status_code=200,
        media_type=media_type,
        headers=headers,
    )
