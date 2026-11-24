from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class PreparedAudio:
    canonical_wav: Path
    playable_mp3: Path
    duration_seconds: float
    sample_rate: int
    channels: int


def _run_cmd(cmd: list[str]) -> str:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd[:4])}): {proc.stderr.strip()}")
    return proc.stdout


def probe_audio(path: Path) -> tuple[float, int, int]:
    out = _run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=sample_rate,channels",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(out or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    streams = data.get("streams") or []
    sr = int(streams[0].get("sample_rate") or 44100) if streams else 44100
    ch = int(streams[0].get("channels") or 2) if streams else 2
    return duration, sr, ch


def prepare_audio(input_path: Path, work_dir: Path) -> PreparedAudio:
    work_dir.mkdir(parents=True, exist_ok=True)
    canonical_wav = work_dir / "canonical_44k.wav"
    playable_mp3 = work_dir / "playable.mp3"

    _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(canonical_wav),
        ]
    )

    encode_mp3(canonical_wav, playable_mp3)
    duration, sr, ch = probe_audio(canonical_wav)
    if duration <= 0.05:
        raise RuntimeError("Uploaded audio is empty or shorter than 50ms.")

    return PreparedAudio(
        canonical_wav=canonical_wav,
        playable_mp3=playable_mp3,
        duration_seconds=duration,
        sample_rate=sr,
        channels=ch,
    )


def encode_mp3(wav_path: Path, mp3_path: Path, bitrate: str = "192k") -> None:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            str(mp3_path),
        ]
    )
