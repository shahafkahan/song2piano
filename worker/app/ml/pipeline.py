from __future__ import annotations

from pathlib import Path
import tempfile
import traceback

import numpy as np
import pretty_midi
import soundfile as sf

from app.ml.audio import prepare_audio
from app.ml.device import resolve_device
from app.ml.engrave import quantize_and_engrave
from app.ml.stems import separate_stems
from app.ml.transcribe import transcribe_audio_to_midi
from songscribe_shared.config import get_settings
from songscribe_shared.db import get_session_factory
from songscribe_shared.events import publish_progress
from songscribe_shared.models import Job, JobStatus
from songscribe_shared.storage import get_storage


def _peak_normalize_wav(src_wav: Path, dst_wav: Path) -> Path:
    data, sr = sf.read(str(src_wav), dtype="float32", always_2d=True)
    peak = float(np.max(np.abs(data)))
    if 0.01 < peak < 0.85:
        norm = np.clip(data * (0.89 / peak), -1.0, 1.0)
        sf.write(str(dst_wav), norm, sr, subtype="PCM_16")
        return dst_wav
    return src_wav


def _merge_raw_midis(paths: list[Path], out_path: Path) -> Path:
    merged = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    for p in paths:
        if not p.is_file() or p.stat().st_size == 0:
            continue
        pm = pretty_midi.PrettyMIDI(str(p))
        for inst in pm.instruments:
            if not inst.is_drum and inst.notes:
                merged.instruments.append(inst)
    merged.write(str(out_path))
    return out_path


def run_transcription_job(job_id: str) -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    storage = get_storage()
    device_info = resolve_device(settings.device)

    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} does not exist.")

        artifacts = dict(job.artifacts_json or {})
        original_key = artifacts.get("original_audio")
        if not original_key:
            raise ValueError(f"Job {job_id} is missing original_audio key.")

        def report(stage: str, progress: int, message: str, status: str = JobStatus.PROCESSING.value) -> None:
            publish_progress(
                db,
                job_id=job_id,
                stage=stage,
                progress=progress,
                status=status,
                message=message,
                device=device_info.label,
            )

        report("preparing", 5, f"Initializing pipeline on {device_info.label}...")

        with tempfile.TemporaryDirectory(prefix=f"songscribe_{job_id[:8]}_") as tmp_str:
            work_dir = Path(tmp_str)
            ext = Path(original_key).suffix or ".wav"
            local_input = work_dir / f"input{ext}"

            try:
                storage.get_file(original_key, local_input)
                report("ffmpeg", 12, "Normalizing audio with FFmpeg (44.1kHz stereo + playable MP3)...")
                prepared = prepare_audio(local_input, work_dir)

                playable_key = f"jobs/{job_id}/audio.mp3"
                storage.put_file(playable_key, prepared.playable_mp3, content_type="audio/mpeg")
                artifacts["playable_audio"] = playable_key
                job.duration_seconds = round(prepared.duration_seconds, 2)
                job.artifacts_json = dict(artifacts)
                db.commit()

                stem_mode = getattr(job, "stem_mode", "auto") or "auto"
                transcription_audio = prepared.canonical_wav
                vocal_midi_path: Path | None = None
                bass_midi_path: Path | None = None
                vocals_wav_path: Path | None = None
                other_wav_path: Path | None = None
                beat_source_audio = prepared.canonical_wav

                if stem_mode == "piano_cover":
                    stem_res = separate_stems(
                        wav_path=prepared.canonical_wav,
                        stems_dir=work_dir / "stems",
                        instrument="piano",
                        device_info=device_info,
                        model_name=settings.demucs_model,
                        stem_mode="piano_cover",
                        progress_cb=lambda pct, msg: report("stems", pct, msg),
                    )
                    stems_keys: dict[str, str] = {}
                    for stem_name, mp3_path in stem_res.stems_mp3.items():
                        s_key = f"jobs/{job_id}/stems/{stem_name}.mp3"
                        storage.put_file(s_key, mp3_path, content_type="audio/mpeg")
                        stems_keys[stem_name] = s_key

                    artifacts["stems"] = stems_keys
                    job.artifacts_json = dict(artifacts)
                    db.commit()

                    vocals_wav_path = stem_res.stems_wav.get("vocals")
                    other_wav_path = stem_res.stems_wav.get("other")

                    # 1. Transcribe accompaniment ('other' stem -> ByteDance Piano)
                    other_raw_mid = work_dir / "other_raw.mid"
                    if stem_res.stem_rms_ratio.get("other", 0.0) >= 0.03 and "other" in stem_res.stems_wav:
                        other_norm = _peak_normalize_wav(
                            stem_res.stems_wav["other"], work_dir / "other_norm.wav"
                        )
                        transcribe_audio_to_midi(
                            audio_path=other_norm,
                            output_midi=other_raw_mid,
                            instrument="piano",
                            device_info=device_info,
                            progress_cb=lambda pct, msg: report("transcribing", 50 + int((pct - 50) * 0.4), f"[Harmony] {msg}"),
                        )
                    else:
                        pretty_midi.PrettyMIDI().write(str(other_raw_mid))

                    # 2. Transcribe singing melody ('vocals' stem -> Basic Pitch ONNX)
                    vocals_raw_mid = work_dir / "vocals_raw.mid"
                    if stem_res.stem_rms_ratio.get("vocals", 0.0) >= 0.04 and "vocals" in stem_res.stems_wav:
                        vocals_norm = _peak_normalize_wav(
                            stem_res.stems_wav["vocals"], work_dir / "vocals_norm.wav"
                        )
                        transcribe_audio_to_midi(
                            audio_path=vocals_norm,
                            output_midi=vocals_raw_mid,
                            instrument="vocals",
                            device_info=device_info,
                            progress_cb=lambda pct, msg: report("transcribing", 64 + int((pct - 50) * 0.35), f"[Vocals] {msg}"),
                        )
                        vocal_midi_path = vocals_raw_mid

                    # 3. Transcribe bass stem if present ('bass' stem -> Basic Pitch ONNX)
                    bass_raw_mid = work_dir / "bass_raw.mid"
                    if stem_res.stem_rms_ratio.get("bass", 0.0) >= 0.08 and "bass" in stem_res.stems_wav:
                        bass_norm = _peak_normalize_wav(
                            stem_res.stems_wav["bass"], work_dir / "bass_norm.wav"
                        )
                        transcribe_audio_to_midi(
                            audio_path=bass_norm,
                            output_midi=bass_raw_mid,
                            instrument="bass",
                            device_info=device_info,
                            progress_cb=lambda pct, msg: report("transcribing", 74, f"[Bass] {msg}"),
                        )
                        bass_midi_path = bass_raw_mid

                    raw_midi_path = _merge_raw_midis(
                        [other_raw_mid, vocals_raw_mid, bass_raw_mid],
                        work_dir / "raw.mid",
                    )
                    # Pass other_raw_mid to engraver so vocal/bass stay cleanly separated
                    raw_for_engraver = other_raw_mid
                    transcription_audio = stem_res.target_audio_wav
                else:
                    if job.separate_stems and stem_mode != "none":
                        stem_res = separate_stems(
                            wav_path=prepared.canonical_wav,
                            stems_dir=work_dir / "stems",
                            instrument=job.instrument,
                            device_info=device_info,
                            model_name=settings.demucs_model,
                            stem_mode=stem_mode,
                            progress_cb=lambda pct, msg: report("stems", pct, msg),
                        )
                        stems_keys = {}
                        for stem_name, mp3_path in stem_res.stems_mp3.items():
                            s_key = f"jobs/{job_id}/stems/{stem_name}.mp3"
                            storage.put_file(s_key, mp3_path, content_type="audio/mpeg")
                            stems_keys[stem_name] = s_key

                        artifacts["stems"] = stems_keys
                        job.artifacts_json = dict(artifacts)
                        db.commit()

                        transcription_audio = stem_res.target_audio_wav
                        if stem_res.used_fallback_mix:
                            report(
                                "stems",
                                47,
                                f"Target stem '{stem_res.selected_stem}' bypassed for full mix while keeping all stems.",
                            )
                    else:
                        report("stems", 45, "Stem separation skipped; transcribing full audio mix.")

                    raw_midi_path = work_dir / "raw.mid"
                    transcribe_audio_to_midi(
                        audio_path=transcription_audio,
                        output_midi=raw_midi_path,
                        instrument=job.instrument,
                        device_info=device_info,
                        progress_cb=lambda pct, msg: report("transcribing", pct, msg),
                    )
                    raw_for_engraver = raw_midi_path
                    # Keep beat_source_audio as full mix (prepared.canonical_wav) so drums/bass anchor beats

                raw_midi_key = f"jobs/{job_id}/transcription.mid"
                storage.put_file(raw_midi_key, raw_midi_path, content_type="audio/midi")
                artifacts["midi"] = raw_midi_key

                engraved = quantize_and_engrave(
                    raw_midi_path=raw_for_engraver,
                    source_audio_path=transcription_audio,
                    work_dir=work_dir,
                    title=job.title,
                    instrument=job.instrument,
                    quantization=job.quantization,
                    requested_bpm=job.requested_bpm,
                    time_signature=job.requested_time_signature or "auto",
                    requested_key=job.requested_key_signature,
                    strict_piano_denoise=(
                        stem_mode == "piano_only"
                        and not (job.separate_stems and transcription_audio == prepared.canonical_wav)
                    ),
                    vocal_midi_path=vocal_midi_path,
                    bass_midi_path=bass_midi_path,
                    vocals_wav_path=vocals_wav_path,
                    other_wav_path=other_wav_path,
                    beat_source_audio_path=beat_source_audio,
                    progress_cb=lambda pct, msg: report("engraving", pct, msg),
                )

                q_midi_key = f"jobs/{job_id}/quantized.mid"
                musicxml_key = f"jobs/{job_id}/score.musicxml"
                pdf_key = f"jobs/{job_id}/score.pdf"
                notes_key = f"jobs/{job_id}/notes.json"

                storage.put_file(q_midi_key, engraved.quantized_midi_path, content_type="audio/midi")
                storage.put_file(
                    musicxml_key,
                    engraved.musicxml_path,
                    content_type="application/vnd.recordare.musicxml+xml",
                )
                storage.put_file(pdf_key, engraved.pdf_path, content_type="application/pdf")
                storage.put_file(notes_key, engraved.notes_json_path, content_type="application/json")

                artifacts["quantized_midi"] = q_midi_key
                artifacts["musicxml"] = musicxml_key
                artifacts["pdf"] = pdf_key
                artifacts["notes_json"] = notes_key

                job.artifacts_json = dict(artifacts)
                job.detected_bpm = engraved.detected_bpm
                job.detected_key = engraved.detected_key
                job.time_signature = engraved.time_signature
                job.note_count = engraved.note_count
                job.measure_count = engraved.measure_count
                job.compute_device = device_info.label
                db.commit()

                report(
                    "completed",
                    100,
                    (
                        f"Transcription complete ({engraved.note_count} notes, "
                        f"{engraved.measure_count} measures, {engraved.detected_bpm} BPM, "
                        f"{engraved.detected_key}, {engraved.resolved_quantization} grid)."
                    ),
                    status=JobStatus.COMPLETED.value,
                )
            except Exception as exc:
                tb = traceback.format_exc(limit=8)
                publish_progress(
                    db,
                    job_id=job_id,
                    stage="failed",
                    progress=job.progress,
                    status=JobStatus.FAILED.value,
                    message=f"Transcription failed: {exc}",
                    device=device_info.label,
                    error_message=tb,
                )
                raise
