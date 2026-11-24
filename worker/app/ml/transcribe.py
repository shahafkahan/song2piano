from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys
import urllib.request

import librosa
import torch

from app.ml.device import DeviceInfo
from songscribe_shared.config import get_settings

PIANO_CKPT_FILENAME = "note_F1=0.9677_pedal_F1=0.9186.pth"
PIANO_CKPT_URL = (
    "https://zenodo.org/record/4034264/files/"
    "CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
)

# Per-instrument Basic Pitch tuning parameters
INSTRUMENT_PRESETS: dict[str, dict[str, float | None]] = {
    "guitar": {
        "onset_threshold": 0.50,
        "frame_threshold": 0.30,
        "minimum_note_length": 70.0,
        "minimum_frequency": 75.0,
        "maximum_frequency": 1400.0,
    },
    "vocals": {
        "onset_threshold": 0.52,
        "frame_threshold": 0.35,
        "minimum_note_length": 90.0,
        "minimum_frequency": 80.0,
        "maximum_frequency": 1200.0,
    },
    "bass": {
        "onset_threshold": 0.48,
        "frame_threshold": 0.30,
        "minimum_note_length": 90.0,
        "minimum_frequency": 35.0,
        "maximum_frequency": 420.0,
    },
    "strings": {
        "onset_threshold": 0.48,
        "frame_threshold": 0.30,
        "minimum_note_length": 80.0,
        "minimum_frequency": 120.0,
        "maximum_frequency": 2600.0,
    },
    "other": {
        "onset_threshold": 0.50,
        "frame_threshold": 0.30,
        "minimum_note_length": 75.0,
        "minimum_frequency": 50.0,
        "maximum_frequency": 3000.0,
    },
}


def resolve_piano_checkpoint(progress_cb: Callable[[int, str], None] | None = None) -> Path:
    settings = get_settings()
    candidates: list[Path] = []
    if settings.piano_model_path:
        candidates.append(Path(settings.piano_model_path))
    candidates.append(Path.home() / "piano_transcription_inference_data" / PIANO_CKPT_FILENAME)
    candidates.append(Path(settings.model_cache_dir) / PIANO_CKPT_FILENAME)

    for cand in candidates:
        if cand.is_file() and cand.stat().st_size > 10_000_000:
            return cand

    dest = Path(settings.model_cache_dir) / PIANO_CKPT_FILENAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    if progress_cb:
        progress_cb(52, "Downloading ByteDance Piano Transcription weights (~172 MB)...")
    tmp = dest.with_suffix(".pth.part")
    urllib.request.urlretrieve(PIANO_CKPT_URL, str(tmp))
    tmp.replace(dest)
    return dest


def _transcribe_piano_bytedance(
    audio_path: Path,
    output_midi: Path,
    device_info: DeviceInfo,
    progress_cb: Callable[[int, str], None] | None = None,
) -> None:
    ckpt_path = resolve_piano_checkpoint(progress_cb=progress_cb)

    # ByteDance piano_transcription_inference executes inference on 'cuda' or 'cpu'
    # (MPS has unsupported ops in some PyTorch versions for this CRNN, so use cuda or cpu).
    pt_device = "cuda" if device_info.device == "cuda" else "cpu"

    if progress_cb:
        progress_cb(56, f"Loading ByteDance piano model on {pt_device.upper()}...")

    # Import lazily so worker startup stays clean
    from piano_transcription_inference import PianoTranscription, sample_rate

    audio, _ = librosa.load(str(audio_path), sr=sample_rate, mono=True)
    if len(audio) == 0:
        raise RuntimeError("Audio track is empty after decoding.")

    if progress_cb:
        progress_cb(62, "Transcribing piano notes, velocities, and sustain pedal...")

    transcriptor = PianoTranscription(
        device=pt_device,
        checkpoint_path=str(ckpt_path),
    )

    try:
        transcriptor.transcribe(audio, str(output_midi))
    except RuntimeError as exc:
        if "out of memory" in str(exc).lower() and pt_device != "cpu":
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if progress_cb:
                progress_cb(66, "GPU memory tight for piano CRNN; retrying on CPU...")
            cpu_transcriptor = PianoTranscription(
                device="cpu",
                checkpoint_path=str(ckpt_path),
            )
            cpu_transcriptor.transcribe(audio, str(output_midi))
        else:
            raise


def _resolve_basic_pitch_onnx() -> Path:
    import basic_pitch

    pkg_dir = Path(basic_pitch.__file__).resolve().parent
    onnx_path = pkg_dir / "saved_models" / "icassp_2022" / "nmp.onnx"
    if not onnx_path.is_file():
        raise FileNotFoundError(
            f"Basic Pitch ONNX model not found at {onnx_path}. "
            "Ensure basic-pitch==0.4.0 is installed."
        )
    return onnx_path


def _transcribe_other_basic_pitch_onnx(
    audio_path: Path,
    output_midi: Path,
    instrument: str,
    device_info: DeviceInfo,
    progress_cb: Callable[[int, str], None] | None = None,
) -> None:
    if "tensorflow" in sys.modules:
        raise RuntimeError("TensorFlow must not be loaded in SongScribe ONNX worker.")

    onnx_path = _resolve_basic_pitch_onnx()
    preset = INSTRUMENT_PRESETS.get(instrument, INSTRUMENT_PRESETS["other"])

    if progress_cb:
        providers_label = ", ".join(device_info.onnx_providers)
        progress_cb(58, f"Loading Basic Pitch ONNX ({providers_label}) for {instrument}...")

    from basic_pitch.inference import Model, predict

    model = Model(str(onnx_path))

    if progress_cb:
        progress_cb(65, f"Running Basic Pitch ONNX transcription for {instrument}...")

    _model_output, midi_data, _note_events = predict(
        audio_path=str(audio_path),
        model_or_model_path=model,
        onset_threshold=float(preset["onset_threshold"] or 0.5),
        frame_threshold=float(preset["frame_threshold"] or 0.3),
        minimum_note_length=float(preset["minimum_note_length"] or 75.0),
        minimum_frequency=preset["minimum_frequency"],
        maximum_frequency=preset["maximum_frequency"],
        melodia_trick=True,
    )
    midi_data.write(str(output_midi))


def transcribe_audio_to_midi(
    audio_path: Path,
    output_midi: Path,
    instrument: str,
    device_info: DeviceInfo,
    progress_cb: Callable[[int, str], None] | None = None,
) -> Path:
    output_midi.parent.mkdir(parents=True, exist_ok=True)

    if instrument == "piano":
        _transcribe_piano_bytedance(
            audio_path=audio_path,
            output_midi=output_midi,
            device_info=device_info,
            progress_cb=progress_cb,
        )
    else:
        _transcribe_other_basic_pitch_onnx(
            audio_path=audio_path,
            output_midi=output_midi,
            instrument=instrument,
            device_info=device_info,
            progress_cb=progress_cb,
        )

    if not output_midi.is_file() or output_midi.stat().st_size == 0:
        raise RuntimeError("Transcription model did not produce a valid MIDI file.")
    return output_midi
