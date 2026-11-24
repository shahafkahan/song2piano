from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from demucs.apply import apply_model
from demucs.pretrained import get_model
import numpy as np
import soundfile as sf
import torch

from app.ml.audio import encode_mp3
from app.ml.device import DeviceInfo

INSTRUMENT_TO_STEM_4S = {
    "piano": "other",
    "guitar": "other",
    "vocals": "vocals",
    "bass": "bass",
    "strings": "other",
    "other": "other",
}

INSTRUMENT_TO_STEM_6S = {
    "piano": "piano",
    "guitar": "guitar",
    "vocals": "vocals",
    "bass": "bass",
    "strings": "other",
    "other": "other",
}


@dataclass(frozen=True)
class StemResult:
    stems_mp3: dict[str, Path]
    target_audio_wav: Path
    used_fallback_mix: bool
    selected_stem: str
    stems_wav: dict[str, Path] = field(default_factory=dict)
    stem_rms_ratio: dict[str, float] = field(default_factory=dict)


_DEMUCS_CACHE: dict[str, torch.nn.Module] = {}


def _get_demucs_model(model_name: str) -> torch.nn.Module:
    if model_name not in _DEMUCS_CACHE:
        model = get_model(model_name)
        model.eval()
        _DEMUCS_CACHE[model_name] = model
    return _DEMUCS_CACHE[model_name]


def _resolve_demucs_model(instrument: str, default_model: str, stem_mode: str = "auto") -> str:
    # Full-song piano cover uses flagship 4-stem htdemucs (vocals + bass + other + drums).
    # Piano-only isolation and guitar use 6-stem htdemucs_6s.
    if stem_mode == "piano_cover":
        return "htdemucs"
    if stem_mode == "piano_only" or instrument == "guitar":
        return "htdemucs_6s"
    return default_model or "htdemucs"


def _run_apply_model(
    model: torch.nn.Module,
    mix: torch.Tensor,
    device: str,
    segment: int | None,
) -> torch.Tensor:
    kwargs: dict[str, object] = {
        "device": device,
        "shifts": 1,
        "split": True,
        "overlap": 0.25,
        "progress": False,
    }
    if segment is not None:
        kwargs["segment"] = segment

    with torch.no_grad():
        return apply_model(model, mix, **kwargs)


def _run_demucs_with_fallback(
    model: torch.nn.Module,
    wav_norm: torch.Tensor,
    device_info: DeviceInfo,
    segment: int | None,
    progress_cb: Callable[[int, str], None] | None = None,
) -> torch.Tensor:
    try:
        return _run_apply_model(model, wav_norm, device_info.device, segment=segment)
    except RuntimeError as exc:
        if "out of memory" in str(exc).lower() and device_info.device != "cpu":
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if progress_cb:
                progress_cb(34, "GPU memory tight during Demucs; retrying stem separation on CPU...")
            return _run_apply_model(model, wav_norm, "cpu", segment=segment)
        raise


def separate_stems(
    wav_path: Path,
    stems_dir: Path,
    instrument: str,
    device_info: DeviceInfo,
    model_name: str = "htdemucs",
    stem_mode: str = "auto",
    progress_cb: Callable[[int, str], None] | None = None,
) -> StemResult:
    stems_dir.mkdir(parents=True, exist_ok=True)
    effective_model = _resolve_demucs_model(instrument, model_name, stem_mode=stem_mode)

    if progress_cb:
        progress_cb(22, f"Loading Demucs ({effective_model}) on {device_info.label}...")

    model = _get_demucs_model(effective_model)
    audio_np, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    if sr != model.samplerate:
        raise RuntimeError(f"Expected {model.samplerate}Hz audio for Demucs, got {sr}Hz.")

    # (channels, samples) -> (1, channels, samples)
    wav_tensor = torch.from_numpy(audio_np.T).unsqueeze(0)
    ref = wav_tensor.mean(dim=1, keepdim=True)
    wav_norm = (wav_tensor - ref.mean()) / (ref.std() + 1e-8)

    if progress_cb:
        progress_cb(30, f"Separating stems with {effective_model} on {device_info.device.upper()}...")

    segment = 7 if "htdemucs" in effective_model else None
    sources = _run_demucs_with_fallback(
        model, wav_norm, device_info, segment=segment, progress_cb=progress_cb
    )

    sources = sources * (ref.std() + 1e-8) + ref.mean()
    sources_np = sources.squeeze(0).cpu().numpy()

    mix_rms = float(np.sqrt(np.mean(audio_np ** 2)) + 1e-9)
    stems_mp3: dict[str, Path] = {}
    stems_wav: dict[str, Path] = {}
    stem_rms: dict[str, float] = {}
    stem_arrays: dict[str, np.ndarray] = {}

    for idx, stem_name in enumerate(model.sources):
        stem_data = np.clip(sources_np[idx].T, -1.0, 1.0)
        stem_wav = stems_dir / f"{stem_name}.wav"
        stem_mp3 = stems_dir / f"{stem_name}.mp3"
        sf.write(str(stem_wav), stem_data, model.samplerate, subtype="PCM_16")
        encode_mp3(stem_wav, stem_mp3)
        stems_wav[stem_name] = stem_wav
        stems_mp3[stem_name] = stem_mp3
        stem_arrays[stem_name] = stem_data
        stem_rms[stem_name] = float(np.sqrt(np.mean(stem_data ** 2)))

    # Quality guard: if 6-stem htdemucs_6s ripped piano body/bass into 'other' with no guitar present,
    # replace the garbled 6s 'piano' stem with flagship 4-stem htdemucs 'other'.
    if instrument == "piano" and effective_model == "htdemucs_6s" and "piano" in stem_rms:
        guitar_to_piano = stem_rms.get("guitar", 0.0) / (stem_rms["piano"] + 1e-8)
        other_to_piano = stem_rms.get("other", 0.0) / (stem_rms["piano"] + 1e-8)
        if guitar_to_piano < 0.25 and other_to_piano > 0.50:
            if progress_cb:
                progress_cb(
                    38,
                    "6-stem piano split leaked body into 'other' (no guitar detected); "
                    "upgrading to flagship 4-stem htdemucs...",
                )
            ht_model = _get_demucs_model("htdemucs")
            if ht_model.samplerate == model.samplerate and "other" in ht_model.sources:
                ht_sources = _run_demucs_with_fallback(
                    ht_model, wav_norm, device_info, segment=7, progress_cb=progress_cb
                )
                ht_sources = ht_sources * (ref.std() + 1e-8) + ref.mean()
                ht_sources_np = ht_sources.squeeze(0).cpu().numpy()
                other_idx = list(ht_model.sources).index("other")
                ht_other_data = np.clip(ht_sources_np[other_idx].T, -1.0, 1.0)
                piano_wav = stems_dir / "piano.wav"
                piano_mp3 = stems_dir / "piano.mp3"
                sf.write(str(piano_wav), ht_other_data, ht_model.samplerate, subtype="PCM_16")
                encode_mp3(piano_wav, piano_mp3)
                stems_wav["piano"] = piano_wav
                stems_mp3["piano"] = piano_mp3
                stem_arrays["piano"] = ht_other_data
                stem_rms["piano"] = float(np.sqrt(np.mean(ht_other_data ** 2)))

    if progress_cb:
        progress_cb(44, f"Separated {len(stems_mp3)} stems ({', '.join(model.sources)}).")

    stem_map = INSTRUMENT_TO_STEM_6S if "piano" in model.sources else INSTRUMENT_TO_STEM_4S
    target_stem = stem_map.get(instrument, "other")
    if target_stem not in stems_wav:
        target_stem = "other" if "other" in stems_wav else next(iter(stems_wav))

    stem_ratios = {k: float(v / mix_rms) for k, v in stem_rms.items()}

    # If isolated stem has negligible energy (<2% of mix RMS), transcribe full mix instead
    ratio = stem_ratios[target_stem]
    if ratio < 0.02:
        return StemResult(
            stems_mp3=stems_mp3,
            target_audio_wav=wav_path,
            used_fallback_mix=True,
            selected_stem=target_stem,
            stems_wav=stems_wav,
            stem_rms_ratio=stem_ratios,
        )

    # Automatic solo-piano bypass: if non-piano stems have < 5% of mix RMS, keep pristine original WAV
    if instrument == "piano" and stem_mode != "piano_cover":
        other_sources = [s for s in model.sources if s != target_stem]
        non_piano_rms = float(np.sqrt(sum(stem_rms[s] ** 2 for s in other_sources)))
        if (non_piano_rms / mix_rms) < 0.05:
            return StemResult(
                stems_mp3=stems_mp3,
                target_audio_wav=wav_path,
                used_fallback_mix=True,
                selected_stem=target_stem,
                stems_wav=stems_wav,
                stem_rms_ratio=stem_ratios,
            )

    # Peak-normalize target stem to -1 dBFS (0.89) if peak is between 0.02 and 0.85
    target_arr = stem_arrays[target_stem]
    peak = float(np.max(np.abs(target_arr)))
    target_wav_out = stems_wav[target_stem]
    if 0.02 < peak < 0.85:
        norm_arr = np.clip(target_arr * (0.89 / peak), -1.0, 1.0)
        norm_path = stems_dir / "target_normalized.wav"
        sf.write(str(norm_path), norm_arr, model.samplerate, subtype="PCM_16")
        target_wav_out = norm_path

    return StemResult(
        stems_mp3=stems_mp3,
        target_audio_wav=target_wav_out,
        used_fallback_mix=False,
        selected_stem=target_stem,
        stems_wav=stems_wav,
        stem_rms_ratio=stem_ratios,
    )
