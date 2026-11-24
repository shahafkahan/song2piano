"""Generate a realistic ~10s polyphonic stereo piano sample WAV for SongScribe demos & tests."""
from __future__ import annotations

from pathlib import Path
import struct
import wave

import numpy as np


def _midi_to_hz(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _synth_piano_note(
    midi: int,
    duration_sec: float,
    velocity: float,
    sr: int,
) -> np.ndarray:
    n_samples = int(duration_sec * sr)
    t = np.arange(n_samples, dtype=np.float64) / sr
    f0 = _midi_to_hz(midi)

    # Rich harmonic profile with slight inharmonicity & faster decay for upper partials
    harmonics = [
        (1, 1.00, 1.9),
        (2, 0.48, 2.8),
        (3, 0.24, 3.8),
        (4, 0.11, 5.2),
        (5, 0.05, 6.8),
    ]
    wave_sig = np.zeros(n_samples, dtype=np.float64)
    attack_len = max(1, int(0.008 * sr))
    attack = np.linspace(0.0, 1.0, attack_len)

    for k, amp, decay in harmonics:
        freq = f0 * k * (1.0 + 0.00018 * (k * k - 1))
        if freq >= sr * 0.45:
            continue
        env = np.exp(-decay * t)
        wave_sig += amp * env * np.sin(2.0 * np.pi * freq * t)

    wave_sig[:attack_len] *= attack
    # Soft release tail at end of note
    release_len = min(n_samples, int(0.045 * sr))
    if release_len > 0:
        wave_sig[-release_len:] *= np.linspace(1.0, 0.0, release_len)

    return wave_sig * velocity


def generate_demo_piano(out_path: Path, sr: int = 44100) -> Path:
    bpm = 108.0
    beat = 60.0 / bpm
    total_beats = 18.0
    total_sec = total_beats * beat + 1.2
    n_total = int(total_sec * sr)

    left = np.zeros(n_total, dtype=np.float64)
    right = np.zeros(n_total, dtype=np.float64)

    # 4-bar progression in C major: C -> Am -> F -> G -> C resolve
    # (start_beat, dur_beats, midi, vel)
    events: list[tuple[float, float, int, float]] = [
        # Bar 1: C major
        (0.0, 2.0, 48, 0.65), (0.0, 2.0, 55, 0.55), (0.5, 1.0, 60, 0.52),
        (0.0, 1.0, 64, 0.72), (1.0, 1.0, 67, 0.75),
        (2.0, 2.0, 48, 0.62), (2.5, 1.0, 55, 0.50),
        (2.0, 0.5, 72, 0.80), (2.5, 0.5, 71, 0.72), (3.0, 1.0, 67, 0.76),
        # Bar 2: A minor
        (4.0, 2.0, 45, 0.66), (4.0, 2.0, 52, 0.54), (4.5, 1.0, 57, 0.52),
        (4.0, 1.0, 69, 0.78), (5.0, 1.0, 64, 0.72),
        (6.0, 2.0, 45, 0.62), (6.5, 1.0, 52, 0.50),
        (6.0, 1.0, 67, 0.74), (7.0, 1.0, 64, 0.70),
        # Bar 3: F major -> G major
        (8.0, 2.0, 41, 0.66), (8.0, 2.0, 48, 0.54), (8.5, 1.0, 53, 0.52),
        (8.0, 1.0, 65, 0.76), (9.0, 1.0, 69, 0.78),
        (10.0, 2.0, 43, 0.66), (10.0, 2.0, 50, 0.55), (10.5, 1.0, 55, 0.54),
        (10.0, 0.5, 71, 0.78), (10.5, 0.5, 69, 0.73), (11.0, 1.0, 67, 0.76),
        # Bar 4: C major cadence
        (12.0, 4.0, 48, 0.70), (12.0, 4.0, 55, 0.58),
        (12.0, 1.0, 64, 0.74), (13.0, 1.0, 67, 0.76), (14.0, 2.0, 72, 0.82),
        (14.0, 2.0, 64, 0.64),
    ]

    for start_b, dur_b, midi, vel in events:
        start_idx = int(start_b * beat * sr)
        note_sig = _synth_piano_note(midi, dur_b * beat + 0.18, vel, sr)
        end_idx = min(n_total, start_idx + len(note_sig))
        seg = note_sig[: end_idx - start_idx]
        # Gentle stereo panning across keyboard (bass left, treble right)
        pan = np.clip((midi - 36) / 60.0, 0.15, 0.85)
        left[start_idx:end_idx] += seg * (1.0 - 0.35 * pan)
        right[start_idx:end_idx] += seg * (0.65 + 0.35 * pan)

    stereo = np.stack([left, right], axis=1)
    peak = float(np.max(np.abs(stereo))) or 1.0
    stereo = (stereo / peak) * 0.86
    pcm = np.clip(stereo * 32767.0, -32768, 32767).astype(np.int16)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return out_path


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    target = root / "frontend" / "public" / "samples" / "demo-piano.wav"
    generate_demo_piano(target)
    print(f"Generated {target} ({target.stat().st_size} bytes)")
