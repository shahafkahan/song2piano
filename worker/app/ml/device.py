from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import onnxruntime as ort
import torch


@dataclass(frozen=True)
class DeviceInfo:
    device: str  # "cuda" | "mps" | "cpu"
    device_name: str
    cuda_available: bool
    mps_available: bool
    vram_gb: float | None
    onnx_providers: tuple[str, ...]

    @property
    def label(self) -> str:
        if self.device == "cuda" and self.vram_gb:
            return f"CUDA ({self.device_name}, {self.vram_gb:.1f} GB)"
        if self.device == "cuda":
            return f"CUDA ({self.device_name})"
        if self.device == "mps":
            return f"MPS ({self.device_name})"
        return "CPU"


@lru_cache(maxsize=4)
def resolve_device(preferred: str = "auto") -> DeviceInfo:
    pref = (preferred or "auto").strip().lower()
    cuda_ok = torch.cuda.is_available()
    mps_ok = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    available_ort = ort.get_available_providers()

    chosen = "cpu"
    if pref == "cuda" and cuda_ok:
        chosen = "cuda"
    elif pref == "mps" and mps_ok:
        chosen = "mps"
    elif pref == "auto":
        if cuda_ok:
            chosen = "cuda"
        elif mps_ok:
            chosen = "mps"

    vram_gb: float | None = None
    device_name = "CPU"
    if chosen == "cuda":
        props = torch.cuda.get_device_properties(0)
        device_name = props.name
        vram_gb = props.total_memory / (1024 ** 3)
    elif chosen == "mps":
        device_name = "Apple Silicon GPU"

    providers: list[str] = []
    if chosen == "cuda" and "CUDAExecutionProvider" in available_ort:
        providers.append("CUDAExecutionProvider")
    elif chosen == "mps" and "CoreMLExecutionProvider" in available_ort:
        providers.append("CoreMLExecutionProvider")
    if "CPUExecutionProvider" in available_ort:
        providers.append("CPUExecutionProvider")
    if not providers:
        providers = list(available_ort)

    return DeviceInfo(
        device=chosen,
        device_name=device_name,
        cuda_available=cuda_ok,
        mps_available=mps_ok,
        vram_gb=vram_gb,
        onnx_providers=tuple(providers),
    )
